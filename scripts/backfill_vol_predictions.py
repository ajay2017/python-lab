"""
Backfill model_predictions with historical vol_forecast_ewma predictions —
Predictive Modeling Shadow Layer Phase 1 (F-234), design doc §1.6b.

Standalone, one-off/rerunnable script — NOT part of the daily cron, NOT wired
into app.py. Per-ticker scope ONLY (currently held tickers): walks a strided
set of historical `as_of` points across up to PREDICTION_BACKFILL_PERIOD
("5y") of price history and, for each, computes and writes a FULLY MATURED
prediction row in one shot — forecast, baseline, AND the already-known
realized outcome — since every backfilled `as_of` point is far enough in the
past that its outcome is already fully realized.

Deliberately does NOT backfill PORTFOLIO scope: that needs actual historical
portfolio weights, which only exist as far back as the logged `trades`
history (~3 months), not 5 years of market data. Portfolio-scope rows are
written only by the live daily cron going forward (cron_runner.py).

Idempotent: rerunning never duplicates rows — relies on the same
(model_name, model_version, scope, ticker, made_at) UNIQUE constraint +
upsert that the live cron's writer uses (db.save_model_predictions_batch).

Run from the Streamlit Cloud terminal (Manage app -> Terminal) or any shell
with the same Supabase env vars the app uses:

    python scripts/backfill_vol_predictions.py

MEASUREMENT-ONLY: nothing this script writes is read by any gate,
recommendation, or the composite score.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import data, db  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    PREDICTION_BACKFILL_PERIOD,
    VOL_FORECAST_EWMA_LAMBDA,
    VOL_FORECAST_HORIZON_DAYS,
)
from stock_analyzer.vol_forecast import forecast_vol_ewma, realized_vol  # noqa: E402

# Stride between sampled as_of dates, in trading-day steps. ~5 trading days
# (horizon / 4) for a 20-day horizon — reduces but does not eliminate window
# overlap between consecutive backfilled rows (design doc §1.6b explicitly
# expects and documents this, rather than claiming full independence).
_STRIDE_TRADING_DAYS = max(1, VOL_FORECAST_HORIZON_DAYS // 4)


def _held_tickers() -> list[str]:
    holdings_df = db.load_holdings()
    if holdings_df is None or holdings_df.empty or "Ticker" not in holdings_df.columns:
        return []
    return sorted({
        str(t).strip().upper() for t in holdings_df["Ticker"].tolist() if str(t).strip()
    })


def _backfill_ticker(ticker: str) -> tuple[int, str | None]:
    """Returns (rows_written, skip_reason). skip_reason is None on success
    (even a success with 0 rows, e.g. history too short for even one
    complete as_of/target window)."""
    try:
        hist = data.fetch_price_history(ticker, period=PREDICTION_BACKFILL_PERIOD)
    except Exception as e:
        return 0, f"fetch failed ({str(e)[:100]})"
    if hist is None or hist.empty or "Close" not in hist.columns:
        return 0, "no price history returned"

    closes = hist["Close"].dropna()
    returns = closes.pct_change().dropna()
    n = len(returns)
    if n <= VOL_FORECAST_HORIZON_DAYS:
        return 0, f"insufficient history ({n} return obs)"

    rows: list[dict] = []
    # i = positional index into `returns` acting as the as_of point. The
    # target window is returns[i+1 : i+1+horizon] — must exist in full, so i
    # only ranges up to n - horizon - 1 (inclusive).
    last_i = n - VOL_FORECAST_HORIZON_DAYS - 1
    for i in range(0, last_i + 1, _STRIDE_TRADING_DAYS):
        hist_slice = returns.iloc[: i + 1]
        forecast = forecast_vol_ewma(hist_slice, lam=VOL_FORECAST_EWMA_LAMBDA)
        baseline = realized_vol(hist_slice.tail(VOL_FORECAST_HORIZON_DAYS))
        if forecast is None or baseline is None:
            continue  # vol_forecast's own >=5-observation floor, not met yet

        target_slice = returns.iloc[i + 1: i + 1 + VOL_FORECAST_HORIZON_DAYS]
        realized = realized_vol(target_slice)
        if realized is None:
            continue

        as_of_date = returns.index[i]
        scored_date = returns.index[i + VOL_FORECAST_HORIZON_DAYS]
        made_at = _to_iso(as_of_date)
        scored_at = _to_iso(scored_date)
        if made_at is None or scored_at is None:
            continue

        rows.append({
            "model_name":        "vol_forecast_ewma",
            "model_version":     "v1",
            "scope":             "ticker",
            "ticker":            ticker,
            "made_at":           made_at,
            "horizon_days":      VOL_FORECAST_HORIZON_DAYS,
            "target_metric":     "realized_vol_20d_annualized",
            "predicted_value":   forecast,
            "baseline_value":    baseline,
            # Historical point-in-time regime tagging for an arbitrary past
            # date is out of scope for this script (no existing function
            # supports it cheaply) — left null rather than fabricated, per
            # the build spec.
            "regime_at_make":    None,
            "realized_value":    realized,
            "scored_at":         scored_at,
            "abs_error":         abs(forecast - realized),
            "baseline_abs_error": abs(baseline - realized),
            "source":            "backfill",
        })

    if not rows:
        return 0, "no as_of points cleared the minimum-observation floor"

    ok = db.save_model_predictions_batch(rows)
    return (len(rows) if ok else 0), (None if ok else "DB write failed (see warning above)")


def _to_iso(ts) -> str | None:
    try:
        return ts.isoformat()
    except Exception:
        try:
            return str(ts)
        except Exception:
            return None


def main() -> None:
    tickers = _held_tickers()
    if not tickers:
        print("No held tickers found (no holdings, or DB unavailable) — nothing to backfill.")
        return

    print(f"Backfilling vol_forecast_ewma for {len(tickers)} held ticker(s): "
          f"{', '.join(tickers)}")
    print(f"Period={PREDICTION_BACKFILL_PERIOD} · stride={_STRIDE_TRADING_DAYS} trading days "
          f"· horizon={VOL_FORECAST_HORIZON_DAYS} trading days")

    total_rows = 0
    skipped: list[str] = []
    for ticker in tickers:
        n_rows, reason = _backfill_ticker(ticker)
        if reason:
            print(f"  {ticker}: SKIPPED — {reason}")
            skipped.append(ticker)
        else:
            print(f"  {ticker}: {n_rows} row(s) written")
        total_rows += n_rows

    print(f"\nBackfill complete: {len(tickers)} ticker(s) processed, "
          f"{total_rows} row(s) written, {len(skipped)} skipped"
          + (f" ({', '.join(skipped)})" if skipped else "") + ".")
    print("PORTFOLIO scope was NOT backfilled by this script — that needs "
          "historical trade-derived weights (design doc §1.6b); the daily "
          "cron writes portfolio-scope rows going forward instead.")


if __name__ == "__main__":
    main()
