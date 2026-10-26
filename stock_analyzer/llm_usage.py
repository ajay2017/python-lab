"""Process-level tally of ``llm.complete`` results, per role.

System Trust's "AI models" section reads it. No Streamlit import. The counters
are process-level, not ``st.session_state``, because the owner decision (D2-3)
is "seen by this process": a session_state counter would see only one browser
session and reset on every refresh. Cron lanes run in their own processes and
are not counted here.
"""
from __future__ import annotations

import copy
import threading

_lock = threading.Lock()
_tally: dict = {}


def _blank() -> dict:
    return {"calls": 0, "errors": 0, "last_error": None, "last_error_at": None,
            "input_tokens": 0, "output_tokens": 0, "fell_back": 0,
            "last_model": None}


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def record(role, result) -> None:
    """Count one result for ``role``. Never raises."""
    try:
        from stock_analyzer import market_time
        failed = not result.ok
        stamp = market_time.now_et().isoformat(timespec="seconds") if failed else None
        usage = getattr(result, "usage", None)
        tin = tout = None
        if isinstance(usage, dict):
            tin, tout = _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))
        with _lock:
            row = _tally.setdefault(role, _blank())
            row["calls"] += 1
            if failed:
                row["errors"] += 1
                row["last_error"] = result.error
                row["last_error_at"] = stamp
            if tin is not None:
                row["input_tokens"] += tin
            if tout is not None:
                row["output_tokens"] += tout
            if getattr(result, "fell_back", False):
                row["fell_back"] += 1
            row["last_model"] = result.model
    except Exception:  # noqa: BLE001 - observability must never break a call
        pass


def snapshot() -> dict:
    """A deep copy: role -> counters."""
    with _lock:
        return copy.deepcopy(_tally)


def reset() -> None:
    """For tests."""
    with _lock:
        _tally.clear()
