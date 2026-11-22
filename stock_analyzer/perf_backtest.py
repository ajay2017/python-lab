"""Portfolio-vs-SPY backtest window for 🥧 Portfolio Overview → 📈 Performance.

Pure (no streamlit). The window is SPY's own last ``n_days`` sessions. A holding
whose price history starts more than a few sessions into the window is LEFT OUT
and reported, never allowed to shrink the window (PerfWindowCollapse: a 2-session
holding used to collapse the "3M" window to one day). The included holdings are
reweighted to 100%. A small start slack (_START_SLACK_SESSIONS) tolerates history
fetches that began a few sessions apart; the window then starts at the latest
included first close (``start_trimmed_sessions`` says by how much).
"""
from __future__ import annotations

import math

import pandas as pd

# Display tolerance for history fetches that start a few sessions apart (cache age,
# provider); NOT a policy threshold.
_START_SLACK_SESSIONS = 5


def _clean(s: pd.Series | None) -> pd.Series:
    """tz-naive, sorted, de-duplicated, NaN-free copy of a close series."""
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    out = pd.to_numeric(s, errors="coerce").dropna().copy()
    if out.empty:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def _left_out_row(ticker: str, series: pd.Series, weight: float) -> dict:
    first = series.index.min() if len(series) else None
    return {
        "ticker": ticker,
        "sessions": int(len(series)),
        "first_date": first.date() if first is not None else None,
        "weight_pct": weight * 100,
    }


def backtest_vs_spy(
    closes: dict[str, pd.Series],
    weights: dict[str, float],
    spy_close: pd.Series,
    n_days: int,
) -> dict:
    """Current-weights backtest of the book against SPY over SPY's last n_days sessions."""
    spy = _clean(spy_close)
    if len(spy) < 2:
        return {"ok": False, "reason": "no_spy"}
    window = list(spy.index[-n_days:]) if n_days > 0 else list(spy.index)
    if len(window) < 2:
        return {"ok": False, "reason": "no_spy"}
    window_short = len(window) < n_days
    spy_window_sessions = len(window)
    # A holding covers the window when its first close falls within the first
    # _START_SLACK_SESSIONS sessions of it (fetch spans can differ by a few days).
    cover_cut = window[min(_START_SLACK_SESSIONS, len(window) - 1)]

    left_out: list[dict] = []
    cands: list[tuple[str, float, pd.Series, pd.Timestamp]] = []
    for ticker, w in weights.items():
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if not (w > 0) or math.isinf(w):  # also drops NaN
            continue
        s = _clean(closes.get(ticker))
        if s.empty or s.index.min() > cover_cut:
            left_out.append(_left_out_row(ticker, s, w))
            continue
        cands.append((ticker, w, s, s.index.min()))

    # Effective start = the latest first close among the covering holdings; if
    # trimming would leave under 2 dates, the latest-starting holding goes out.
    trimmed: list = []
    while cands:
        eff = max(window[0], max(c[3] for c in cands))
        trimmed = [d for d in window if d >= eff]
        if len(trimmed) >= 2:
            break
        latest = max(range(len(cands)), key=lambda i: cands[i][3])
        t, w, s, _first = cands.pop(latest)
        left_out.append(_left_out_row(t, s, w))
        trimmed = []

    included: list[str] = []
    rets: dict[str, pd.Series] = {}
    used_w: dict[str, float] = {}
    for t, w, s, _first in cands:
        base = float(s.asof(trimmed[0]))
        if not (base > 0):
            left_out.append(_left_out_row(t, s, w))
            continue
        rets[t] = (s.reindex(trimmed, method="ffill") / base - 1) * 100
        used_w[t] = w
        included.append(t)

    if not included:
        return {"ok": False, "reason": "none_included", "left_out": left_out}
    left_out_weight_pct = float(sum(r["weight_pct"] for r in left_out))

    total_w = sum(used_w.values())
    port_ret = pd.Series(0.0, index=trimmed)
    for t in included:
        port_ret = port_ret + rets[t] * used_w[t]
    port_ret = port_ret / total_w

    spy_path = spy.reindex(trimmed, method="ffill")
    spy_ret = (spy_path / float(spy_path.iloc[0]) - 1) * 100

    port_final = float(port_ret.iloc[-1])
    spy_final = float(spy_ret.iloc[-1])
    return {
        "ok": True,
        "dates": trimmed,
        "port_ret": port_ret,
        "spy_ret": spy_ret,
        "port_final": port_final,
        "spy_final": spy_final,
        "alpha": port_final - spy_final,
        "included": included,
        "left_out": left_out,
        "left_out_weight_pct": left_out_weight_pct,
        "window_short": window_short,
        "spy_window_sessions": spy_window_sessions,
        "start_trimmed_sessions": len(window) - len(trimmed),
    }
