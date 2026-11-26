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

The view decision and the plain-text notes for the left-out holdings live below
(view_state, left_out_note, thin_note): one place decides what a correlation
screen may show, and the phrase naming a young holding is the one risk_window
uses (holdings_phrase), so the two disclosures on 🔗 Risk Analysis agree.

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
from stock_analyzer.risk_window import classify_holdings, holdings_phrase, weight_pct


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
            left_out = [dict(x, weight_pct=weight_pct(w.get(x["ticker"]))) for x in left_out]
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


# ── view decision and notes (CorrYoung C2) ───────────────────────────────────
def need_of(coverage) -> int:
    """The per-holding floor a coverage dict was built with. A memoised pre-C1
    dict (no min_returns) or a non-number reads as CORR_MIN_OBS_TRUSTED."""
    v = coverage.get("min_returns") if isinstance(coverage, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return CORR_MIN_OBS_TRUSTED
    return int(v)


def left_out_items(coverage) -> list[dict]:
    """The coverage's left_out entries that are dicts with a ticker, in order.
    A non-dict coverage or a pre-C1 dict (no left_out) reads as []."""
    if not isinstance(coverage, dict):
        return []
    raw = coverage.get("left_out")
    if not isinstance(raw, (list, tuple)):
        return []
    return [x for x in raw if isinstance(x, dict) and x.get("ticker") is not None]


def short_history_clause(
    items: list[dict], *, need, of_history: bool, pronoun: bool = False,
) -> str:
    """"ANT has only 4 daily returns (20 needed)"; several names:
    "ANT and BBB have only 4 and 7 daily returns (20 needed)". of_history adds
    "of price history" after "returns". pronoun=True reads a single name as
    "it has only ..." (for a sentence that already named it). `items` are
    short_history left_out dicts (at least one). Plain text."""
    names = [str(x["ticker"]) for x in items]
    if pronoun and len(items) == 1:
        names = ["it"]
    counts = [str(int(x["n_returns"])) for x in items]

    def _join(parts):
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]

    one = len(items) == 1
    unit = "daily return" if (one and counts[0] == "1") else "daily returns"
    tail = " of price history" if of_history else ""
    return f"{_join(names)} {'has' if one else 'have'} only {_join(counts)} {unit}{tail} ({need} needed)"


def unconfirmed_reasons(items: list[dict], *, need) -> str:
    """The reason clause (no brackets) for a left-out list in a sentence that
    already names the holdings: one name reads "it has only 4 daily returns of
    price history, 20 needed" / "it has no price history"; several read
    "ANT has only 4 daily returns of price history and XYZ has no price history,
    20 needed" (the floor is quoted only when a young holding is among them)."""
    one = len(items) == 1
    parts, young = [], False
    for x in items:
        who = "it" if one else str(x["ticker"])
        if x.get("reason") == "short_history":
            young = True
            n = int(x["n_returns"])
            parts.append(f"{who} has only {n} daily return{'' if n == 1 else 's'} of price history")
        else:
            parts.append(f"{who} has no price history")
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{text}, {need} needed" if young else text


def left_out_note(coverage) -> str | None:
    """Plain-text disclosure of the holdings left out of the correlation reading,
    or None when none were (or the coverage is not a dict / has no left_out).
    No dollar signs, no markdown. The size part is shown only when every
    left-out holding has a numeric weight. Never raises."""
    items = left_out_items(coverage)
    if not items:
        return None
    try:
        need = need_of(coverage)
        sized = all(weight_pct(x.get("weight_pct")) is not None for x in items)
        phrase = holdings_phrase(items, need=need, with_size=sized)
        tail = ("its pairs are not shown and it is not part of the score."
                if len(items) == 1 else
                "their pairs are not shown and they are not part of the score.")
        return f"Correlations measured without {phrase}; {tail}"
    except Exception:
        return "Correlations were measured without some holdings."


def thin_note(coverage) -> str:
    """Plain-text note for a sample too thin to measure. Never says the holdings
    move together; names the shared-return count and the floor. Never raises."""
    n = coverage.get("n_obs") if isinstance(coverage, dict) else None
    try:
        count = f"only {int(n)} shared daily returns"
    except (TypeError, ValueError):
        count = "too few shared daily returns"
    return (
        f"⛔ Correlations can't be measured on this sample: {count} "
        f"({need_of(coverage)} needed). A sample this small is unreliable in both "
        "directions: unrelated stocks can read as moving together and a real pair "
        "can read as independent, so no pairs, clusters or diversification score "
        "are shown. Adds on any pair that crossed the line stay paused until it "
        "can be measured."
    )


def view_state(corr_df, corr_coverage, corr_unchecked) -> dict:
    """What a correlation screen may show:
    {"state", "notes": [str], "left_out", "unchecked_rest"}.

      withheld -- the sample is THIN (portfolio.correlation_sample_state), or the
                  matrix is empty while holdings were left out (fewer than 2
                  counted): show only `notes` (no pairs, heatmap, clusters, score).
      none     -- the matrix is empty and nothing was left out (fewer than 2
                  holdings have prices): show as before. An empty matrix with a
                  coverage of None also reads "none"; the Home producer's
                  exception path lands there too and cannot be told apart here
                  (C4 handles it).
      unknown  -- a matrix but no readable sample size (coverage not a dict, or
                  n_obs unusable): show as before, notes [] (the app keeps its
                  existing caption).
      partial  -- TRUSTED and a holding was left out, or corr_unchecked names one:
                  notes = [left_out_note] when something was left out.
      full     -- TRUSTED, nothing left out and corr_unchecked empty or None. A
                  `corr_unchecked` of None (couldn't check) with a trusted sample
                  reads "full" here: util.correlation_unchecked_note's "unknown
                  this session" caption carries that case, not this function.
    `left_out` is the coverage's left-out holdings. `unchecked_rest` is the names
    in corr_unchecked that are NOT in left_out (a failed price download), as a
    list ([] when none); None when corr_unchecked is None. Render
    util.correlation_unchecked_note only for these; the left-out names are
    already said by `notes`.
    A memoised pre-C1 coverage dict (no left_out) reads as nothing left out.
    Never raises; bad input reads as "unknown"."""
    try:
        # Function-local: portfolio imports this module.
        from stock_analyzer import portfolio as _portfolio
        items = left_out_items(corr_coverage)
        empty = not isinstance(corr_df, pd.DataFrame) or corr_df.empty
        sample = _portfolio.correlation_sample_state(corr_coverage)
        if isinstance(corr_unchecked, (list, tuple)):
            lo_names = {str(x["ticker"]).strip().upper() for x in items}
            rest, seen = [], set()
            for t in corr_unchecked:
                if t is None or not str(t).strip():
                    continue
                u = str(t).strip().upper()
                if u not in lo_names and u not in seen:
                    seen.add(u)
                    rest.append(u)
        else:
            rest = None

        def _out(state, notes, left):
            return {"state": state, "notes": notes, "left_out": left, "unchecked_rest": rest}

        if sample == _portfolio.CORR_SAMPLE_THIN or (empty and items):
            notes = [thin_note(corr_coverage)]
            lo = left_out_note(corr_coverage)
            if lo is not None:
                notes.append(lo)
            return _out("withheld", notes, list(items))
        if empty:
            return _out("none", [], [])
        if sample != _portfolio.CORR_SAMPLE_TRUSTED:
            return _out("unknown", [], [])
        unchecked = isinstance(corr_unchecked, (list, tuple)) and len(corr_unchecked) > 0
        if items or unchecked:
            lo = left_out_note(corr_coverage)
            return _out("partial", [lo] if lo is not None else [], list(items))
        return _out("full", [], [])
    except Exception:
        return {"state": "unknown", "notes": [], "left_out": [], "unchecked_rest": None}
