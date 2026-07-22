"""
Backfill analyst_coverage.price_at_article_date — Analyst Research Accountability,
Phase 1 (one-time, run manually).

Standalone script — NOT a Streamlit page. Reads every analyst_coverage row where
price_at_article_date IS NULL, fetches the next trading-day close via yfinance,
and writes it back to Supabase. Idempotent: rows already backfilled are skipped
on every subsequent run because the query only pulls NULL rows.

Run from the Streamlit Cloud terminal (Manage app -> Terminal) against the same
Supabase instance the app uses:

    python scripts/backfill_analyst_prices.py

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


def main() -> None:
    df = db.load_analyst_coverage(limit=10000)
    if df is None or df.empty:
        print("No analyst_coverage rows found — nothing to backfill.")
        return

    pending = df[df["price_at_article_date"].isna()]
    if pending.empty:
        print("All analyst_coverage rows already have price_at_article_date — nothing to do.")
        return

    updated = 0
    skipped = 0
    for _, row in pending.iterrows():
        ticker = str(row.get("ticker") or "").strip().upper()
        article_date = _parse_article_date(row.get("article_date"))
        row_id = row.get("id")
        if not ticker or article_date is None or row_id is None:
            print(f"WARN: skipping row {row_id} — missing ticker/article_date")
            skipped += 1
            continue

        # Shared with the Research Scorecard's live "Fetch now" button
        # (stock_analyzer/analyst_intel.py) so batch and on-demand fetches
        # never drift apart.
        price = analyst_intel.fetch_anchor_price(ticker, article_date)
        if price is None:
            print(f"WARN: no price found for {ticker} on {article_date} — skip")
            skipped += 1
            continue
        if db.update_analyst_coverage_price(row_id, price):
            print(f"OK: {ticker} {article_date} -> ${price:.2f}")
            updated += 1
        else:
            print(f"WARN: DB update failed for {ticker} row {row_id} — skip")
            skipped += 1

    print(f"\nBackfill complete: {updated} updated, {skipped} skipped.")


if __name__ == "__main__":
    main()
