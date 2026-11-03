"""Decide whether a Trade Journal holdings rebuild may run, and whether it may
save an EMPTY book (bug register HoldWipe).

An empty `save_holdings` sweeps every holdings row AND every manual stop
(owner-entered, can't be rebuilt). The lenient `db.load_trades()` returns an
empty frame on a failed read, so a rebuild fed from it could wipe both. The
callers read with the strict `db.load_trades_or_none()` and pass the result
here; `allow_empty` is bound to the read so no caller can pass a bare True.
"""
from __future__ import annotations

import pandas as pd


def decide(fresh: "pd.DataFrame | None", *, rows_just_inserted: bool) -> dict:
    """Return {"action": "unreadable" | "empty" | "rebuild", "allow_empty": bool}.

    - `fresh is None`: the journal couldn't be read -> "unreadable"; never
      rebuild, never save.
    - empty frame right after an insert (`rows_just_inserted`): an empty
      journal can't be true when we just wrote a row -> treated as
      "unreadable", not as an empty book.
    - empty frame, nothing inserted: a genuinely empty journal -> "empty";
      nothing to replay, holdings left as they are (`allow_empty` False).
    - non-empty frame: "rebuild". `allow_empty` is True because a successful
      read of a non-empty journal that replays to zero shares is a real
      sell-everything.
    """
    if fresh is None:
        return {"action": "unreadable", "allow_empty": False}
    if fresh.empty:
        if rows_just_inserted:
            return {"action": "unreadable", "allow_empty": False}
        return {"action": "empty", "allow_empty": False}
    return {"action": "rebuild", "allow_empty": True}
