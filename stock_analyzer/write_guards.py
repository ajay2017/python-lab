"""Pure decisions for what the market-day DB writers may persist or delete.

Found by the 2026-10-14 impossible-states sweep (docs/sql/impossible-states-
audit.sql) against production data:

* `db.save_daily_snapshot` swept (deleted) every ticker for the date that was
  not in the batch being written. Both writers drop a holding they could not
  PRICE from that batch (the app via its live-price map, the cron via
  compute_eod's `px > 0` filter, and the cron still writes on a 3A' partial
  price outage). So "couldn't price it today" was recorded as "no longer
  held" -- MU vanished from the 2026-06-25 snapshot with no trade either side,
  which corrupts the next day's P&L baseline and the snapshot history that
  E2 alpha attribution is accumulating.

* The app's writers for recommendations / gate_suppressions / exit_signals
  stamped `today_et()` with no trading-day check (the cron lanes already skip
  non-trading days), so opening the app on a weekend recorded 250+ "market
  day" rows priced at Friday's close -- each one counted as a real call by
  Engine Track Record, the Gate Suppression Ledger and ENTER_NOW grading.

Both decisions live here, with no I/O, so they are unit-testable; db.py only
applies them.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable


def snapshot_sweep_keep(written: Iterable[str],
                        held_tickers: "Iterable[str] | None") -> "list[str] | None":
    """Tickers a daily_snapshots sweep must KEEP for the date being written.

    The sweep deletes every row for the date whose ticker is NOT in the
    returned list. A ticker is kept if it was written in this batch OR is
    still held -- a held ticker missing from the batch was merely unpriced,
    and its existing row (an earlier same-day write with a real close) is
    the best record available. Only a ticker that is neither written nor held
    -- a genuine same-day exit -- is swept.

    `held_tickers=None` means the caller could not establish what is held,
    so nothing may be deleted: returns None and the caller skips the sweep.
    A lingering exited row is recoverable on the next write; a deleted held
    position is not.
    """
    if held_tickers is None:
        return None
    keep = {str(t).strip().upper() for t in written if str(t).strip()}
    keep |= {str(t).strip().upper() for t in held_tickers if str(t).strip()}
    return sorted(keep)


def _as_date(value) -> "date | None":
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def split_non_trading_rows(rows: list[dict], date_key: str) -> "tuple[list[dict], int]":
    """Split rows into (kept, n_dropped) by whether `row[date_key]` is an NYSE
    trading day. A market-day record (a recommendation, an exit signal, a gate
    suppression) dated on a weekend or holiday describes a session that did
    not happen.

    A row whose date is missing or unparseable is KEPT: deciding validity is
    the writer's existing normalization, not this filter's job, and dropping
    it here would hide that defect behind a "non-trading day" count.
    """
    from stock_analyzer.data import is_trading_day

    kept: list[dict] = []
    dropped = 0
    for r in rows or []:
        d = _as_date((r or {}).get(date_key))
        if d is not None and not is_trading_day(d):
            dropped += 1
            continue
        kept.append(r)
    return kept, dropped
