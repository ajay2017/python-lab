"""The book's daily return series, with young holdings left out and disclosed.

Why this exists (RiskListwiseYoung, docs/plans/risk-young-holding.md): the old
builders (risk.compute_portfolio_risk_metrics, portfolio.portfolio_return_series)
cut the price frame listwise, so ONE holding with a few closes shrank every
metric of the book to that name's few days. This module is the one decision:

  * A holding is COUNTED when it has >= min_returns real daily returns (the same
    day its own beta turns measurable, risk.beta_vs_market). A younger holding
    ("short_history") and a held name with no usable prices ("no_prices") are
    LEFT OUT of the series; their weight is spread over the others.
  * Book return on day t = sum(w_i * r_i,t) / sum(w_i) over the counted holdings
    that have a return on t. Interior gaps are forward-filled (a halted name),
    as today; nothing is filled before a holding's first close.
  * A day is KEPT only when that day's unmeasured weight (left out + counted but
    not trading yet) is <= max_unmeasured_pct (inclusive). Fewer than min_days
    kept days, or nothing counted -> None (the existing offline contract; never
    an empty dict or an all-None dict).

Pure: pandas / numpy / constants only. No clock (it counts rows in the frames it
is given), no streamlit, no db. Never raises on bad input: it returns
(None, an "unmeasured" coverage that lists whatever it could).
"""
from __future__ import annotations

import math

import pandas as pd

from stock_analyzer.constants import (
    BETA_MIN_RETURNS,
    PORTFOLIO_RISK_MIN_DAYS,
    PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
)

# Float tolerance only (a share of exactly the cap must count as at the cap even
# after a float sum such as 6.1 + 3.9); not a decision value.
_SHARE_EPS = 1e-9


# ── helpers ──────────────────────────────────────────────────────────────────
def _session_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Index -> tz-naive midnight timestamps of each bar's own session date.
    A tz-aware index drops its zone WITHOUT converting (the date the provider
    stamped is the session date in the exchange's own zone), then is normalised,
    so an intraday-stamped bar merges into its session. Converting to ET first
    (held_stop._et_dates does that for US bars) would move every bar of a
    non-US listing (Asia/Seoul, Europe/*, a UTC-midnight index) onto the
    previous calendar day. A tz-naive index is taken as exchange-local dates."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _closes_by_session(data) -> pd.Series | None:
    """One row per session date (a duplicate date keeps the last row), sorted,
    NaN closes dropped; None when the holding has no usable Close history.
    Reads the frame the way risk.compute_portfolio_risk_metrics does:
    data["df"] when present, else data["history"]."""
    if not isinstance(data, dict):
        return None
    hist = data.get("df") if data.get("df") is not None else data.get("history")
    if hist is None or not isinstance(hist, pd.DataFrame):
        return None
    if hist.empty or "Close" not in hist.columns:
        return None
    closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
    if closes.empty:
        return None
    closes = pd.Series(closes.to_numpy(dtype=float), index=_session_dates(closes.index))
    closes = closes.sort_index(kind="stable")
    closes = closes[~closes.index.duplicated(keep="last")]
    return closes if len(closes) else None


def _weight_pct(value) -> float | None:
    """A Weight (%) cell as a finite float, else None (unknown size)."""
    try:
        w = float(value)
    except (TypeError, ValueError):
        return None
    return w if math.isfinite(w) else None


def _book_weights(port_df) -> dict[str, float | None]:
    """Ticker -> Weight (%) in port_df row order; a repeated ticker keeps the last
    value at its first position (the old dict-assignment behaviour). A NaN or
    unreadable weight is None."""
    out: dict[str, float | None] = {}
    for _, row in port_df.iterrows():
        out[row["Ticker"]] = _weight_pct(row["Weight (%)"])
    return out


def _coverage(
    state: str,
    left_out: list[dict],
    window_returns: int,
    *,
    min_returns: int,
    max_unmeasured_pct: float,
    unmeasured_pct: float | None,
    late_start: list[str] | None = None,
) -> dict:
    return {
        "state": state,
        "left_out": left_out,
        "left_out_weight_pct": round(
            sum(x["weight_pct"] for x in left_out if x.get("weight_pct") is not None), 4
        ),
        "window_returns": window_returns,
        # Extra context for the note and for wiring (all additive):
        "min_returns": min_returns,
        "max_unmeasured_pct": max_unmeasured_pct,
        "unmeasured_pct": unmeasured_pct,
        "late_start": late_start or [],
    }


def _fallback_left_out(port_df, held_data, min_returns) -> list[dict]:
    """Best-effort listing for the 'never raises' path. Never raises itself."""
    try:
        out = []
        for t, w in _book_weights(port_df).items():
            closes = _closes_by_session((held_data or {}).get(t))
            n = 0 if closes is None else max(len(closes) - 1, 0)
            if closes is None:
                out.append({"ticker": t, "reason": "no_prices", "n_returns": 0, "weight_pct": w})
            elif n < min_returns:
                out.append({"ticker": t, "reason": "short_history", "n_returns": n, "weight_pct": w})
        return out
    except Exception:
        return []


# ── the decision ─────────────────────────────────────────────────────────────
def book_returns(
    port_df,
    held_data,
    *,
    min_returns: int = BETA_MIN_RETURNS,
    max_unmeasured_pct: float = PORTFOLIO_RISK_UNMEASURED_MAX_PCT,
    min_days: int = PORTFOLIO_RISK_MIN_DAYS,
) -> tuple[pd.Series | None, dict]:
    """The book's daily return series and what it left out.

    Returns (series | None, coverage). The series is indexed by session date (tz-naive).
    coverage = {"state": "full" | "partial" | "unmeasured",
                "left_out": [{"ticker", "reason": "short_history" | "no_prices",
                              "n_returns", "weight_pct"}],
                "left_out_weight_pct", "window_returns", ...extra keys}.
    "unmeasured" whenever the series is None. "partial" when something is left
    out or a counted holding was not trading yet on some kept day; "full" when
    every holding is counted and spans every kept day (then the series equals the
    old listwise result)."""
    try:
        return _book_returns(
            port_df, held_data, min_returns, max_unmeasured_pct, min_days
        )
    except Exception:
        # Bad input of any shape: the offline contract, with what we can list.
        return None, _coverage(
            "unmeasured",
            _fallback_left_out(port_df, held_data, min_returns),
            0,
            min_returns=min_returns,
            max_unmeasured_pct=max_unmeasured_pct,
            unmeasured_pct=None,
        )


def _book_returns(port_df, held_data, min_returns, max_unmeasured_pct, min_days):
    def _none(left_out, unmeasured_pct=None, late=None):
        return None, _coverage(
            "unmeasured", left_out, 0,
            min_returns=min_returns, max_unmeasured_pct=max_unmeasured_pct,
            unmeasured_pct=unmeasured_pct, late_start=late,
        )

    if (
        port_df is None
        or not isinstance(port_df, pd.DataFrame)
        or port_df.empty
        or "Ticker" not in port_df.columns
        or "Weight (%)" not in port_df.columns
    ):
        return _none([])
    held = held_data if isinstance(held_data, dict) else {}

    weights = _book_weights(port_df)

    counted: dict[str, pd.Series] = {}   # ticker -> closes (session-dated)
    left_out: list[dict] = []
    for t, w in weights.items():
        closes = _closes_by_session(held.get(t))
        if closes is None:
            left_out.append({"ticker": t, "reason": "no_prices", "n_returns": 0, "weight_pct": w})
            continue
        n_ret = max(len(closes) - 1, 0)   # real closes only; gaps are not returns
        if n_ret < min_returns:
            left_out.append({"ticker": t, "reason": "short_history", "n_returns": n_ret, "weight_pct": w})
        elif w is None:
            # Priced, long enough, but its size is unknown: it cannot be weighted
            # into the series. Listed (size unknown), left out of the share.
            left_out.append({"ticker": t, "reason": "no_weight", "n_returns": n_ret, "weight_pct": None})
        else:
            counted[t] = closes

    static_pct = sum(x["weight_pct"] for x in left_out if x["weight_pct"] is not None)

    if not counted:
        return _none(left_out, unmeasured_pct=static_pct if left_out else None)

    wp = pd.Series({t: weights[t] for t in counted}, dtype=float)   # % of book, row order
    total_counted = float(wp.sum())
    if total_counted == 0:
        return _none(left_out, unmeasured_pct=static_pct)

    # Union of the counted holdings' sessions; interior (and trailing) gaps are
    # forward-filled exactly as the old listwise builder did; nothing before a
    # holding's first close.
    prices = pd.DataFrame(counted).sort_index().ffill()
    # Same expression as DataFrame.pct_change() on a NaN-free frame.
    rets = (prices / prices.shift(1) - 1).iloc[1:]
    if rets.empty:
        return _none(left_out, unmeasured_pct=static_pct)

    avail = rets.notna()
    avail_w = avail.mul(wp, axis=1).sum(axis=1)
    unmeasured_day = static_pct + (total_counted - avail_w)
    kept = (unmeasured_day <= max_unmeasured_pct + _SHARE_EPS) & (avail_w > 0)
    best_share = float(unmeasured_day.min())

    n_kept = int(kept.sum())
    if n_kept < min_days:
        return _none(left_out, unmeasured_pct=best_share)

    rets = rets[kept]
    avail = avail[kept]
    avail_w = avail_w[kept]

    # Normalised weights over the counted set, summed in port_df order: the same
    # arithmetic as the old builder, so a book with no young holding is
    # bit-for-bit the old series on days every holding trades.
    frac = {t: float(w) / 100.0 for t, w in wp.items()}
    frac_total = sum(frac.values())
    wn = {t: w / frac_total for t, w in frac.items()}
    full = pd.Series(0.0, index=rets.index)
    for t, w in wn.items():
        full += rets[t] * w
    partial = rets.fillna(0.0).mul(wp, axis=1).sum(axis=1) / avail_w
    all_avail = avail.all(axis=1)
    series = full.where(all_avail, partial)
    series.name = None

    late = [t for t in counted if not bool(avail[t].all())]
    state = "partial" if (left_out or late) else "full"
    return series, _coverage(
        state, left_out, int(len(series)),
        min_returns=min_returns, max_unmeasured_pct=max_unmeasured_pct,
        unmeasured_pct=float(unmeasured_day[kept].max()), late_start=late,
    )


# ── disclosure text ──────────────────────────────────────────────────────────
def _names(items: list[dict]) -> str:
    parts = []
    for x in items:
        w = x.get("weight_pct")
        share = f"{w:.1f}% of the book" if w is not None else "size unknown"
        reason = x.get("reason")
        if reason == "short_history":
            n = int(x["n_returns"])
            need = x["_need"]
            detail = f"{n} trading day{'' if n == 1 else 's'} since its first close, {need} needed"
        elif reason == "no_weight":
            detail = None
        else:
            detail = "no price history"
        inner = share if detail is None else f"{share}: {detail}"
        parts.append(f"{x.get('ticker')} ({inner})")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


_GENERIC_NOTE = "Portfolio risk was measured without some holdings."


def coverage_note(coverage) -> str | None:
    """Plain-text disclosure for a coverage dict (no dollar signs, no markdown);
    None when the book was measured in full (or there is nothing to say). A
    malformed or older coverage dict that is not "full" reads the generic line
    instead of raising."""
    if not isinstance(coverage, dict):
        return None
    try:
        return _coverage_note(coverage)
    except Exception:
        return _GENERIC_NOTE


def _number(value):
    """A real number (not bool) or ValueError, so a present-but-None setting
    cannot print as 'None needed'."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    return value


def _coverage_note(coverage: dict) -> str | None:
    state = coverage.get("state")
    if state == "full" or state is None:
        return None
    need = _number(coverage.get("min_returns", BETA_MIN_RETURNS))
    cap = _number(coverage.get("max_unmeasured_pct", PORTFOLIO_RISK_UNMEASURED_MAX_PCT))
    raw_left = coverage.get("left_out")
    left_out = [dict(x, _need=need) for x in (raw_left if raw_left is not None else [])]
    raw_late = coverage.get("late_start")
    late = raw_late if raw_late is not None else []

    if state == "partial":
        if left_out:
            text = (
                f"Portfolio risk measured without {_names(left_out)}; "
                "its weight is spread over the other holdings."
                if len(left_out) == 1 else
                f"Portfolio risk measured without {_names(left_out)}; "
                "their weight is spread over the other holdings."
            )
            if late:
                text += (
                    f" Early days are also measured without {', '.join(late)} "
                    "(not trading yet)."
                )
            return text
        if late:
            return (
                f"Early days of the portfolio risk window are measured without "
                f"{', '.join(late)} (not trading yet); its weight is spread over the "
                "other holdings."
                if len(late) == 1 else
                f"Early days of the portfolio risk window are measured without "
                f"{', '.join(late)} (not trading yet); their weight is spread over the "
                "other holdings."
            )
        return None

    # unmeasured
    share = coverage.get("unmeasured_pct")
    if left_out and share is not None and share > cap:
        return (
            f"Portfolio risk can't be measured: {_names(left_out)} "
            f"leave out {share:.1f}% of the book, more than the {cap:g}% limit."
            if len(left_out) > 1 else
            f"Portfolio risk can't be measured: {_names(left_out)} "
            f"leaves out {share:.1f}% of the book, more than the {cap:g}% limit."
        )
    if left_out:
        return (
            f"Portfolio risk can't be measured yet: not enough price history "
            f"across the book (left out: {_names(left_out)})."
        )
    return "Portfolio risk can't be measured yet: not enough price history across the book."
