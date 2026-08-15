"""
Backfill analyst_coverage.price_at_article_date — Analyst Research Accountability,
Phase 1.

Standalone script — NOT a Streamlit page. Reads every analyst_coverage row where
price_at_article_date IS NULL, fetches the next trading-day close via yfinance,
and writes it back to Supabase. Idempotent: rows already backfilled are skipped
on every subsequent run because the query only pulls NULL rows.

Normally you do NOT need to run this by hand: the `maintenance` cron lane
(`cron_runner.py`, ALERT_RUN_MODE=maintenance) calls `run_backfill()` on a
schedule, so saved research gets its anchor price filled automatically. Run it
manually only for an immediate catch-up, from any shell with the same Supabase
env vars the app uses:

    python scripts/backfill_analyst_prices.py

NOTE: Railway's Console shell is NOT the app's environment (minimal PATH, no app
dependencies, unset LD_LIBRARY_PATH) — that is why this became a cron lane.

Awareness-only data — price_at_article_date never feeds valuation_score() or any
gate; it exists purely to anchor the Research Scorecard's return-since-call math.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import analyst_intel, db  # noqa: E402


def _parse_article_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def run_backfill(log=print) -> dict:
    """Fill `price_at_article_date` on every analyst_coverage row still missing
    one, returning a summary dict.

    Naturally self-limiting — it only ever touches rows where the anchor price
    is NULL, so a recurring run costs one cheap query once the table is caught
    up. That is what makes it safe for the `maintenance` cron lane to call on a
    schedule without any extra "already done" bookkeeping.

    `log` is injected so the cron lane can route output through its own
    timestamped `_log` instead of bare stdout.
    """
    # load_analyst_coverage returns an EMPTY frame on failure, not None, so an
    # offline DB is indistinguishable from "no rows" once we're past this point.
    # That was tolerable while a human ran this and watched the output; as an
    # unattended lane it would report a clean success over a dead DB. Check
    # explicitly so offline is logged as offline.
    if not db.has_db():
        log("DB unavailable — skipping analyst anchor-price backfill (not a no-op success).")
        return {"updated": 0, "skipped_count": 0, "pending": 0, "offline": True}

    df = db.load_analyst_coverage(limit=10000)
    if df is None or df.empty:
        log("No analyst_coverage rows found — nothing to backfill.")
        return {"updated": 0, "skipped_count": 0, "pending": 0, "offline": False}

    pending = df[df["price_at_article_date"].isna()]
    if pending.empty:
        log("All analyst_coverage rows already have price_at_article_date — nothing to do.")
        return {"updated": 0, "skipped_count": 0, "pending": 0, "offline": False}

    updated = 0
    skipped = 0
    for _, row in pending.iterrows():
        ticker = str(row.get("ticker") or "").strip().upper()
        article_date = _parse_article_date(row.get("article_date"))
        row_id = row.get("id")
        if not ticker or article_date is None or row_id is None:
            log(f"WARN: skipping row {row_id} — missing ticker/article_date")
            skipped += 1
            continue

        # Shared with the Research Scorecard's live "Fetch now" button
        # (stock_analyzer/analyst_intel.py) so batch and on-demand fetches
        # never drift apart.
        price = analyst_intel.fetch_anchor_price(ticker, article_date)
        if price is None:
            log(f"WARN: no price found for {ticker} on {article_date} — skip")
            skipped += 1
            continue
        if db.update_analyst_coverage_price(row_id, price):
            log(f"OK: {ticker} {article_date} -> ${price:.2f}")
            updated += 1
        else:
            log(f"WARN: DB update failed for {ticker} row {row_id} — skip")
            skipped += 1

    log(f"Backfill complete: {updated} updated, {skipped} skipped.")
    # NB: `skipped_count` is an int here; the vol backfill's summary uses
    # `skipped` for a list[str] of tickers. Named differently on purpose so the
    # two summaries can never be confused at a call site.
    return {"updated": updated, "skipped_count": skipped,
            "pending": int(len(pending)), "offline": False}


def main() -> None:
    run_backfill()


if __name__ == "__main__":
    main()
