"""
Behavioral Fingerprint — Concept A (F-193), My Edge 4th tab.

Observation-only, sample-gated statistics over the SAME `recommendations` /
`trades` substrate as `recommendations_history.py` and `decision_quality.py`
(per the plan's own coordination note: this must not become a parallel
logger). Buy-side only for v1 — exit-side TRIM/EXIT signals have no
historical capture (see docs/plans/next-evolution-strategy.md, Concept A,
Week-1 data-readiness audit).

Every pattern function returns None when either compared bucket has fewer
than `min_n` rows — never present a directional finding at small N. These
are correlations observed in the investor's own past decisions, not a
verdict on them, and the engine never reads any of these outputs: nothing
here re-ranks, re-scores, or gates a recommendation.

All functions are pure computation (no Streamlit, no DB calls, no fetches)
and defensively swallow malformed/missing input by returning None rather
than raising — a single bad row must not crash the tab.
"""

from typing import Optional
import pandas as pd


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        return None if x != x else x  # NaN check
    except (TypeError, ValueError):
        return None


# ── Pattern 1 — momentum / recency-chasing proxy ─────────────────────────────

def momentum_recency_pattern(
    matched: list[dict], min_n: int, meaningful_delta_pp: float = 5.0
) -> Optional[dict]:
    """
    Median-split actionable recs by `momentum_score` (already known at signal
    time — zero new price fetches) and compare action_rate between the
    high-momentum half and the low-momentum half.

    `meaningful_delta_pp` is a display-copy threshold only (caller should pass
    constants.BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP) — it decides whether
    `direction` reads "chases"/"fades" vs "flat"; it never suppresses the card
    (that's `min_n`'s job).

    Returns None when either bucket (or the total scored pool) is below
    `min_n` in size. Otherwise:
        {"high": {n, n_acted, action_rate},
         "low":  {n, n_acted, action_rate},
         "delta_pp": float,
         "direction": "chases" | "fades" | "flat"}
    """
    try:
        if not matched:
            return None
        scored = [
            r for r in matched
            if isinstance(r, dict) and _safe_float(r.get("momentum_score")) is not None
        ]
        if len(scored) < min_n * 2:
            return None

        scored_sorted = sorted(scored, key=lambda r: _safe_float(r["momentum_score"]))
        n = len(scored_sorted)
        mid = n // 2
        median_val = (
            _safe_float(scored_sorted[mid]["momentum_score"])
            if n % 2 == 1
            else (
                _safe_float(scored_sorted[mid - 1]["momentum_score"])
                + _safe_float(scored_sorted[mid]["momentum_score"])
            ) / 2.0
        )

        high = [r for r in scored if _safe_float(r["momentum_score"]) >= median_val]
        low = [r for r in scored if _safe_float(r["momentum_score"]) < median_val]
        if len(high) < min_n or len(low) < min_n:
            return None

        def _bucket_stats(items: list[dict]) -> dict:
            n_i = len(items)
            n_acted = sum(1 for r in items if bool(r.get("acted_on")))
            return {
                "n": n_i,
                "n_acted": n_acted,
                "action_rate": round(n_acted / n_i * 100.0, 1) if n_i else 0.0,
            }

        high_stats = _bucket_stats(high)
        low_stats = _bucket_stats(low)
        delta_pp = round(high_stats["action_rate"] - low_stats["action_rate"], 1)
        if delta_pp > meaningful_delta_pp:
            direction = "chases"
        elif delta_pp < -meaningful_delta_pp:
            direction = "fades"
        else:
            direction = "flat"

        return {
            "high": high_stats,
            "low": low_stats,
            "delta_pp": delta_pp,
            "direction": direction,
            "median": median_val,
        }
    except Exception:
        return None


# ── Live decision-moment mirror — BUY side (momentum) ────────────────────────
# Classifies a SINGLE ticker's momentum score into the SAME >= median
# high/low bucket boundary momentum_recency_pattern uses internally, so the
# 📒 Log Trade page can mirror this pattern at the instant a BUY is logged —
# not only retrospectively on the 🧬 Behavioral Fingerprint tab. Never reads
# the engine's recommendation, never gates the trade write.

def classify_live_buy_momentum(
    this_momentum_score, matched: list[dict], min_n: int, meaningful_delta_pp: float = 5.0
) -> Optional[dict]:
    """
    Returns None when `this_momentum_score` is None/NaN, or when the
    underlying momentum_recency_pattern is below `min_n` per bucket (the
    same sample-size floor as the retrospective card). Otherwise:
        {"bucket": "high" | "low",
         "high": {n, n_acted, action_rate},
         "low":  {n, n_acted, action_rate},
         "median": float}
    `bucket` is which side THIS score falls on (>= median -> "high"); the
    caller renders both buckets' rates regardless, per the locked copy spec.
    """
    try:
        score = _safe_float(this_momentum_score)
        if score is None:
            return None
        pattern = momentum_recency_pattern(matched, min_n, meaningful_delta_pp=meaningful_delta_pp)
        if pattern is None:
            return None
        median_val = pattern["median"]
        bucket = "high" if score >= median_val else "low"
        return {
            "bucket": bucket,
            "high": pattern["high"],
            "low": pattern["low"],
            "median": median_val,
        }
    except Exception:
        return None


# ── Pattern 2 — conviction-tier follow-through ───────────────────────────────

def conviction_tier_pattern(
    matched: list[dict], strong_buy_floor: float, min_n: int
) -> Optional[dict]:
    """
    Compare action_rate for Strong Buy (composite_score >= strong_buy_floor)
    vs plain Buy (below it) actionable recs.

    Returns None when either bucket is below `min_n`. Otherwise:
        {"strong_buy": {n, n_acted, action_rate},
         "buy":        {n, n_acted, action_rate},
         "delta_pp": float}
    """
    try:
        if not matched:
            return None
        scored = [
            r for r in matched
            if isinstance(r, dict) and _safe_float(r.get("composite_score")) is not None
        ]
        if not scored:
            return None

        strong = [r for r in scored if _safe_float(r["composite_score"]) >= strong_buy_floor]
        buy = [r for r in scored if _safe_float(r["composite_score"]) < strong_buy_floor]
        if len(strong) < min_n or len(buy) < min_n:
            return None

        def _bucket_stats(items: list[dict]) -> dict:
            n_i = len(items)
            n_acted = sum(1 for r in items if bool(r.get("acted_on")))
            return {
                "n": n_i,
                "n_acted": n_acted,
                "action_rate": round(n_acted / n_i * 100.0, 1) if n_i else 0.0,
            }

        strong_stats = _bucket_stats(strong)
        buy_stats = _bucket_stats(buy)
        delta_pp = round(strong_stats["action_rate"] - buy_stats["action_rate"], 1)

        return {
            "strong_buy": strong_stats,
            "buy": buy_stats,
            "delta_pp": delta_pp,
        }
    except Exception:
        return None


# ── Pattern 3 — opening-window entry timing ──────────────────────────────────

def opening_window_pattern(
    enriched: list[dict], opening_window_min: int, min_n: int
) -> Optional[dict]:
    """
    Compare average `alpha_pct` (SPY-adjusted outcome) between trades entered
    within `opening_window_min` minutes of the 9:30 ET open ("opening") vs
    all other rows with a resolvable `et_time` ("later").

    Caller is responsible for: pre-filtering `enriched` to acted + graded
    (`outcome_pct is not None and not outcome_maturing`) rows, and attaching
    an `et_time` field (a `datetime.time`, or an (hour, minute) tuple) to
    each row already converted to US/Eastern — this function does no
    timezone math itself.

    Returns None when either bucket is below `min_n`. Otherwise:
        {"opening": {n, avg_alpha_pct},
         "later":   {n, avg_alpha_pct},
         "delta_pp": float}
    """
    try:
        if not enriched:
            return None

        def _minutes_after_open(et_time) -> Optional[int]:
            try:
                if hasattr(et_time, "hour") and hasattr(et_time, "minute"):
                    h, m = et_time.hour, et_time.minute
                else:
                    h, m = et_time[0], et_time[1]
                return (int(h) * 60 + int(m)) - (9 * 60 + 30)
            except Exception:
                return None

        rows = []
        for r in enriched:
            if not isinstance(r, dict):
                continue
            et_time = r.get("et_time")
            if et_time is None:
                continue
            mins = _minutes_after_open(et_time)
            if mins is None:
                continue
            alpha = _safe_float(r.get("alpha_pct"))
            if alpha is None:
                continue
            rows.append({"mins": mins, "alpha_pct": alpha})

        if not rows:
            return None

        opening = [r for r in rows if 0 <= r["mins"] < opening_window_min]
        later = [r for r in rows if not (0 <= r["mins"] < opening_window_min)]
        if len(opening) < min_n or len(later) < min_n:
            return None

        def _bucket_stats(items: list[dict]) -> dict:
            n_i = len(items)
            avg = round(sum(r["alpha_pct"] for r in items) / n_i, 2) if n_i else None
            return {"n": n_i, "avg_alpha_pct": avg}

        opening_stats = _bucket_stats(opening)
        later_stats = _bucket_stats(later)
        delta_pp = (
            round(opening_stats["avg_alpha_pct"] - later_stats["avg_alpha_pct"], 2)
            if (opening_stats["avg_alpha_pct"] is not None and later_stats["avg_alpha_pct"] is not None)
            else None
        )

        return {
            "opening": opening_stats,
            "later": later_stats,
            "delta_pp": delta_pp,
        }
    except Exception:
        return None


# ── Exit-side Pattern 1 — signal response rate by signal_type ────────────────

def signal_response_rate_pattern(
    exit_signals_df, trades_df, act_window_days: int, min_n: int
) -> Optional[dict]:
    """
    For each signal_type (WATCH/TRIM/EXIT/RISK_OFF), compute what fraction of
    signals were followed by a SELL trade on the same ticker within
    `act_window_days` calendar days of the signal date.

    Returns None when exit_signals_df is empty, trades_df has no SELL rows, or
    no signal_type reaches `min_n` signals.  Otherwise a dict keyed by
    signal_type, each value:
        {"n_signals": int, "n_acted": int, "action_rate": float}
    """
    try:
        if exit_signals_df is None or (hasattr(exit_signals_df, "empty") and exit_signals_df.empty):
            return None
        if trades_df is None or trades_df.empty:
            return None

        sells = trades_df[trades_df["action"] == "SELL"].copy()
        if sells.empty:
            return None

        # Build a set of (ticker, sell_date) pairs for fast lookup.
        sells["_sell_date"] = pd.to_datetime(sells["traded_at"], utc=True, errors="coerce", format="ISO8601").dt.date
        sells = sells.dropna(subset=["_sell_date"])
        sell_pairs = set(zip(sells["ticker"].str.upper(), sells["_sell_date"]))

        window = pd.Timedelta(days=act_window_days)
        result = {}

        for sig_type, group in exit_signals_df.groupby("signal_type"):
            n_signals = len(group)
            n_acted = 0
            for _, row in group.iterrows():
                try:
                    sig_date = pd.to_datetime(row["signal_date"]).date()
                except Exception:
                    continue
                ticker = str(row.get("ticker", "")).upper()
                if not ticker:
                    continue
                # Check every calendar day in [sig_date, sig_date + act_window_days]
                acted = False
                for offset in range(act_window_days + 1):
                    check_date = (
                        pd.Timestamp(sig_date) + pd.Timedelta(days=offset)
                    ).date()
                    if (ticker, check_date) in sell_pairs:
                        acted = True
                        break
                if acted:
                    n_acted += 1

            if n_signals >= min_n:
                result[str(sig_type)] = {
                    "n_signals": n_signals,
                    "n_acted": n_acted,
                    "action_rate": round(n_acted / n_signals, 4) if n_signals else 0.0,
                }

        return result if result else None
    except Exception:
        return None


# ── Exit-side Pattern 2 — response lag for acted signals ─────────────────────

def signal_lag_pattern(
    exit_signals_df, trades_df, act_window_days: int, min_n: int
) -> Optional[dict]:
    """
    Among signals that WERE acted on (a SELL within `act_window_days` calendar
    days), how many days did it take?

    Returns None when there are no matching pairs or no signal_type reaches
    `min_n` acted signals.  Otherwise a dict keyed by signal_type, each value:
        {"n_acted": int, "median_lag_days": float, "pct_acted_day1": float}
    where `pct_acted_day1` is the percentage acted same-day or next-day (lag ≤ 1).
    """
    try:
        if exit_signals_df is None or (hasattr(exit_signals_df, "empty") and exit_signals_df.empty):
            return None
        if trades_df is None or trades_df.empty:
            return None

        sells = trades_df[trades_df["action"] == "SELL"].copy()
        if sells.empty:
            return None

        sells["_sell_date"] = pd.to_datetime(sells["traded_at"], utc=True, errors="coerce", format="ISO8601").dt.date
        sells = sells.dropna(subset=["_sell_date"])
        # Build a mapping ticker -> sorted list of sell dates for fast lookup.
        from collections import defaultdict
        sell_dates_by_ticker: dict = defaultdict(list)
        for _, row in sells.iterrows():
            sell_dates_by_ticker[str(row["ticker"]).upper()].append(row["_sell_date"])
        for t in sell_dates_by_ticker:
            sell_dates_by_ticker[t] = sorted(sell_dates_by_ticker[t])

        lags_by_type: dict = defaultdict(list)

        for _, row in exit_signals_df.iterrows():
            try:
                sig_date = pd.to_datetime(row["signal_date"]).date()
            except Exception:
                continue
            sig_type = str(row.get("signal_type", ""))
            ticker = str(row.get("ticker", "")).upper()
            if not ticker or not sig_type:
                continue
            window_end = (pd.Timestamp(sig_date) + pd.Timedelta(days=act_window_days)).date()
            # Find the earliest SELL on or after sig_date within the window.
            for sd in sell_dates_by_ticker.get(ticker, []):
                if sd < sig_date:
                    continue
                if sd > window_end:
                    break
                lag = (sd - sig_date).days
                lags_by_type[sig_type].append(lag)
                break  # earliest only

        result = {}
        for sig_type, lags in lags_by_type.items():
            n = len(lags)
            if n < min_n:
                continue
            import statistics
            median_lag = round(statistics.median(lags), 1)
            pct_day1 = round(sum(1 for l in lags if l <= 1) / n * 100.0, 1)
            result[sig_type] = {
                "n_acted": n,
                "median_lag_days": median_lag,
                "pct_acted_day1": pct_day1,
            }

        return result if result else None
    except Exception:
        return None


# ── Live decision-moment mirror — SELL side (active exit signal) ────────────
# Mirrors the Exit Signal Response cards at the instant a SELL is logged on
# 📒 Log Trade — reuses signal_response_rate_pattern / signal_lag_pattern for
# the specific signal_type currently active on this ticker rather than
# introducing a parallel computation. Never gates the trade write.

def latest_active_signal_type(
    exit_signals_df, ticker, as_of_date, act_window_days: int
) -> Optional[str]:
    """
    Most recent `signal_type` for `ticker` in `exit_signals_df` whose
    `signal_date` is within `act_window_days` days of `as_of_date`
    (inclusive boundary — exactly `act_window_days` old still counts as
    active; `act_window_days + 1` does not). Defensive: bad/empty/missing-
    column input or a lookup error returns None, never raises.
    """
    try:
        if exit_signals_df is None or (hasattr(exit_signals_df, "empty") and exit_signals_df.empty):
            return None
        if not ticker or as_of_date is None:
            return None
        for col in ("ticker", "signal_date", "signal_type"):
            if col not in exit_signals_df.columns:
                return None

        ticker_u = str(ticker).upper()
        rows = exit_signals_df[exit_signals_df["ticker"].astype(str).str.upper() == ticker_u]
        if rows.empty:
            return None

        best_date = None
        best_type = None
        for _, row in rows.iterrows():
            try:
                sig_date = pd.to_datetime(row["signal_date"]).date()
            except Exception:
                continue
            age = (as_of_date - sig_date).days
            if age < 0 or age > act_window_days:
                continue
            sig_type = row.get("signal_type")
            if not sig_type:
                continue
            if best_date is None or sig_date > best_date:
                best_date = sig_date
                best_type = str(sig_type)

        return best_type
    except Exception:
        return None


def classify_live_sell_signal(
    signal_type, exit_signals_df, trades_df, act_window_days: int, min_n: int
) -> Optional[dict]:
    """
    Reuses signal_response_rate_pattern + signal_lag_pattern for the single
    `signal_type` currently active on the ticker being sold, so the live
    mirror can never disagree with the retrospective Exit Signal Response
    cards on the same underlying computation.

    Returns None when `signal_type` is falsy, or when that type's sample is
    below `min_n` in either underlying pattern. Otherwise:
        {"action_rate": float (0-1 fraction, same scale as
                                signal_response_rate_pattern),
         "n_signals": int,
         "median_lag_days": float}
    """
    try:
        if not signal_type:
            return None
        rate_pattern = signal_response_rate_pattern(
            exit_signals_df, trades_df, act_window_days, min_n
        )
        if rate_pattern is None or signal_type not in rate_pattern:
            return None
        lag_pattern = signal_lag_pattern(
            exit_signals_df, trades_df, act_window_days, min_n
        )
        if lag_pattern is None or signal_type not in lag_pattern:
            return None

        rate_stats = rate_pattern[signal_type]
        lag_stats = lag_pattern[signal_type]
        return {
            "action_rate": rate_stats["action_rate"],
            "n_signals": rate_stats["n_signals"],
            "median_lag_days": lag_stats["median_lag_days"],
        }
    except Exception:
        return None


# ── Exit-side Pattern 3 — escalation sequences ignored ───────────────────────

def escalation_ignored_pattern(
    exit_signals_df, trades_df, act_window_days: int, min_n: int
) -> Optional[dict]:
    """
    How often does the user hold through a signal escalation (WATCH→TRIM,
    WATCH→EXIT, or TRIM→EXIT on the same ticker) without selling between the
    first and second signal?

    Returns None when fewer than 2 signal rows exist, or when the total
    escalation-event count is below `min_n`.  Otherwise:
        {"n_escalations": int, "n_ignored": int, "ignored_rate": float}
    """
    try:
        if exit_signals_df is None or (hasattr(exit_signals_df, "empty") and exit_signals_df.empty):
            return None
        if len(exit_signals_df) < 2:
            return None

        # Escalation pairs: (lower-severity, higher-severity)
        escalation_pairs = {("WATCH", "TRIM"), ("WATCH", "EXIT"), ("TRIM", "EXIT")}

        sells_df = None
        sell_dates_by_ticker: dict = {}
        if trades_df is not None and not trades_df.empty:
            sells_df = trades_df[trades_df["action"] == "SELL"].copy()
            if not sells_df.empty:
                sells_df["_sell_date"] = pd.to_datetime(
                    sells_df["traded_at"], utc=True, errors="coerce", format="ISO8601"
                ).dt.date
                sells_df = sells_df.dropna(subset=["_sell_date"])
                from collections import defaultdict
                _sell_sets: dict = defaultdict(set)
                for _, row in sells_df.iterrows():
                    _sell_sets[str(row["ticker"]).upper()].add(row["_sell_date"])
                sell_dates_by_ticker = dict(_sell_sets)

        n_escalations = 0
        n_ignored = 0

        for ticker, grp in exit_signals_df.groupby("ticker"):
            ticker_str = str(ticker).upper()
            # Parse dates and sort.
            rows = []
            for _, row in grp.iterrows():
                try:
                    sd = pd.to_datetime(row["signal_date"]).date()
                except Exception:
                    continue
                sig_type = str(row.get("signal_type", ""))
                rows.append((sd, sig_type))
            rows.sort(key=lambda x: x[0])

            # Check every (earlier, later) pair that forms an escalation.
            # Counts are pair-weighted: WATCH→TRIM→EXIT on one ticker = 3 pairs.
            # ignored_rate reflects pair frequency, not distinct episodes.
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    earlier_date, earlier_type = rows[i]
                    later_date, later_type = rows[j]
                    if later_date <= earlier_date:
                        continue
                    if (earlier_type, later_type) not in escalation_pairs:
                        continue
                    # Check for any SELL between the two signal dates (inclusive).
                    ticker_sells = sell_dates_by_ticker.get(ticker_str, set())
                    sold_between = any(
                        earlier_date <= sd <= later_date for sd in ticker_sells
                    )
                    n_escalations += 1
                    if not sold_between:
                        n_ignored += 1

        if n_escalations < min_n:
            return None

        return {
            "n_escalations": n_escalations,
            "n_ignored": n_ignored,
            "ignored_rate": round(n_ignored / n_escalations, 4) if n_escalations else 0.0,
        }
    except Exception:
        return None
