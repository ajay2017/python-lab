"""
Predictive Modeling Shadow Layer — Phase 2 earnings-move-magnitude helpers
(F-234).

Pure logic. Predicts the UNSIGNED magnitude of a single-session earnings-day
price move — never a directional call, never a stock-level expected-return
point estimate (the §5.8 invariant, same as Phase 1's volatility model).
Nothing here feeds any gate, recommendation, or the composite score; it
feeds only the quarantined 🔬 Model Lab page via the `model_predictions`
ledger. `predicted_value` reuses the app's own already-live
`earnings_advisor._estimate_move` heuristic verbatim — no new model is
designed here.

See docs/plans/predictive-modeling-shadow-layer.md §Phase 2 design for the
full design and leakage-guard rationale. Two functions in this module
(`resolve_upcoming_earnings`) legitimately call the `data` module's pure
fetch functions — that's the data layer, not DB/Streamlit — everything
else is a plain array/scalar transform with no I/O.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stock_analyzer import data as _data


def resolve_upcoming_earnings(ticker: str, today: date, lookout_days: int) -> tuple[str | None, str]:
    """Resolve the soonest upcoming earnings date for `ticker` within
    `lookout_days` of `today`, mirroring app.py's
    `_cached_held_earnings_dates` FMP-first-then-yfinance-fallback merge
    pattern, but headless (no `@st.cache_data`, no Streamlit — this runs
    from `cron_runner.py`).

    Returns `(event_date_iso, when)` where `when` is `"bmo"`/`"amc"`/`""`.
    Tries the market-wide FMP calendar first (carries timing); if `ticker`
    isn't in that result, falls back to the per-ticker yfinance date-only
    fetch (`when` is always `""` for that path — yfinance carries no BMO/AMC
    signal). Returns `(None, "")` when neither source has anything.
    Never raises — both underlying fetches already degrade to `[]`/`None`
    on failure; this function adds no new exception surface.
    """
    tkr = str(ticker or "").strip().upper()
    if not tkr:
        return (None, "")
    from_str = today.isoformat()
    to_str = (today + timedelta(days=lookout_days)).isoformat()
    try:
        for row in (_data.fetch_earnings_calendar(from_str, to_str) or []):
            if str(row.get("ticker", "")).strip().upper() == tkr:
                d = str(row.get("date", ""))[:10]
                if d:
                    when = str(row.get("when") or "").strip().lower()
                    return (d, when if when in ("bmo", "amc") else "")
    except Exception:
        pass
    try:
        fallback = _data.fetch_next_earnings(tkr)
    except Exception:
        fallback = None
    if fallback:
        return (str(fallback)[:10], "")
    return (None, "")


def baseline_from_history(past_moves: list[float], k: int) -> float | None:
    """Median of the most recent `k` values in `past_moves` (caller's
    responsibility to pass them already-ordered, most-recent-last).

    Returns `None` (survivorship exclusion) when fewer than `k` values are
    available — NEVER defaults to a sector constant or a partial-sample
    median, which would silently understate the sample this baseline is
    actually resting on."""
    if not past_moves or k <= 0 or len(past_moves) < k:
        return None
    recent = pd.Series([float(v) for v in past_moves[-k:]], dtype=float)
    return float(recent.median())


def realized_move(price_history_df, event_date: str, when: str) -> float | None:
    """Split-adjusted close-to-close move across an earnings print, as an
    unsigned percentage.

    BMO ("before open"): `close_before` = the last close STRICTLY before
    `event_date`; `close_after` = the close ON `event_date`.
    AMC ("after close"): `close_before` = the close ON `event_date`;
    `close_after` = the first close STRICTLY after `event_date`.

    Returns `None` when `when` is not exactly `"bmo"` or `"amc"` (never
    guesses on unknown timing — this is a genuine invariant of THIS
    function, not just a caller convention, so a caller that forgets the
    write-time/maturation-time exclusion still can't get a fabricated
    number out of it), or when either bracketing session's bar is missing
    from `price_history_df`.

    `price_history_df` must have a `Close` column and a datetime-like
    index (or a `Date`-like index convertible via `pd.to_datetime`) —
    same shape as `data.fetch_price_history`'s return. This function is
    ONLY ever safe to call once the post-print bar exists in
    `price_history_df` (i.e. at maturation time, never at write time) —
    the caller enforces that by construction (see cron_runner.py's
    `_mature_earnings_predictions`, which is the only caller)."""
    if when not in ("bmo", "amc"):
        return None
    if price_history_df is None or getattr(price_history_df, "empty", True):
        return None
    if "Close" not in price_history_df.columns:
        return None
    try:
        ev = pd.Timestamp(event_date).normalize()
    except Exception:
        return None
    try:
        idx = pd.to_datetime(price_history_df.index).normalize()
    except Exception:
        return None
    closes = pd.Series(price_history_df["Close"].to_numpy(dtype=float), index=idx)
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()

    if when == "bmo":
        before_slice = closes[closes.index < ev]
        if before_slice.empty:
            return None
        close_before = float(before_slice.iloc[-1])
        on_slice = closes[closes.index == ev]
        if on_slice.empty:
            return None
        close_after = float(on_slice.iloc[-1])
    else:  # "amc"
        on_slice = closes[closes.index == ev]
        if on_slice.empty:
            return None
        close_before = float(on_slice.iloc[-1])
        after_slice = closes[closes.index > ev]
        if after_slice.empty:
            return None
        close_after = float(after_slice.iloc[0])

    if close_before == 0 or not pd.notna(close_before) or not pd.notna(close_after):
        return None
    return abs(close_after / close_before - 1.0) * 100.0


def is_write_eligible(days_until_event: int, lead_days: int) -> bool:
    """True iff `1 <= days_until_event <= lead_days`. Never writes on the
    print day itself or after (days_until_event <= 0 — that would be
    computable only with knowledge that shouldn't exist yet at write time
    in a same-day-close scenario, and is simply too late to be useful as a
    pre-print prediction), and never further out than `lead_days` (keeps
    the prediction close enough to the print that `_estimate_move`'s
    inputs are still fresh)."""
    try:
        d = int(days_until_event)
        lead = int(lead_days)
    except (TypeError, ValueError):
        return False
    return 1 <= d <= lead


def is_reschedule(frozen_event_date: str, fresh_next_event_date: str | None,
                   min_gap_days: int) -> bool:
    """True only if a FRESH lookup at maturation time finds a next-earnings
    date that is CLOSER than `min_gap_days` calendar days after the frozen
    `frozen_event_date` — i.e. the frozen date was a postponement of the
    same print, not a genuine subsequent quarterly print (~90 days out).

    Returns `False` (never a reschedule) when `fresh_next_event_date` is
    `None` — absence of a fresh date must never be read as a reschedule
    signal (the offline-sentinel contract: "couldn't find one" is not
    "found one nearby"). Never raises on a malformed date string — treated
    as "can't tell, so don't withdraw".

    A gap of EXACTLY 0 (fresh == frozen) is also never a reschedule — it
    means the same still-scheduled date, not a postponement. This is
    already guaranteed by the caller only invoking this AFTER the frozen
    date has passed (cron_runner.py's maturation gate runs first, so a
    fresh lookup can no longer return the frozen date itself), but the
    `0 <` (not `0 <=`) lower bound is kept here too as defense-in-depth —
    a caller that mistakenly ran this before that gate (exactly the
    2026-09-07 bug: withdrew every still-upcoming row, same run it was
    written, because gap==0 satisfied the old `0 <=` bound) gets one more
    layer that can't misfire on an unchanged date."""
    if fresh_next_event_date is None:
        return False
    try:
        frozen = pd.Timestamp(frozen_event_date).normalize()
        fresh = pd.Timestamp(fresh_next_event_date).normalize()
    except Exception:
        return False
    gap_days = (fresh - frozen).days
    return 0 < gap_days < int(min_gap_days)
