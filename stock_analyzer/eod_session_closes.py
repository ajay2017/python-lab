"""The session closes the EOD carry writer may trust (SlidingStop C3; pure, no I/O).

`held_stop_carry.rows_to_write` uses `session_closes` ({TICKER: Close of the `as_of`
session}) only for its seed guard: a stop already breached by the day's own close is
never SEEDED. A forced EOD run before the close (`ALERT_EOD_HOUR_ET`, ET) sees a PARTIAL
bar, so its "close" is not the session's close and must not decide a seed. Before the
cutoff this returns an empty map: the seed guard then refuses every seed (an unknown
close never seeds) while a valid saved row still advances.

Extracted from `cron_runner._eod_held_stop_carry` so the comparison against
`ALERT_EOD_HOUR_ET` is unit-tested here (no test imports `cron_runner`'s entrypoint
logic; `check_antipatterns.py`'s POLICY_DECISION_IN_RENDER rule).
"""
from __future__ import annotations

from stock_analyzer.constants import ALERT_EOD_HOUR_ET


def session_closes_for_write(closes, now_et) -> tuple[dict, bool]:
    """(closes, after_close). `after_close` is True when `now_et` (an ET datetime) is
    at or after hour `ALERT_EOD_HOUR_ET`; only then are `closes` returned (a dict; any
    other value reads as no closes), else `{}`. Never raises: an unusable `now_et`
    counts as before the close."""
    try:
        after = int(now_et.hour) >= int(ALERT_EOD_HOUR_ET)
    except (AttributeError, TypeError, ValueError):
        after = False
    if not after:
        return {}, False
    return (closes if isinstance(closes, dict) else {}), True
