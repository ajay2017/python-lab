"""Home's Act Today pointer (Home redesign P2, docs/plans/home-redesign.md's
2026-10-06 "P1 + P2 designed" section) — a one-line pointer at the very top
of Home reporting the already-computed Act Today state, before the Pre-Market
Intel panel and the rest of the page's preamble can push it below the fold.

Pure, no Streamlit/DB/network. Reads the SAME `act_today_view()` result
Home's own Act Today section renders from — this module never calls
`act_today_view()` itself and must never import or call
`decision_bucket.split_defensive` directly (only `bucket_act_by_type`), or it
would reopen the exact count-drift bug `act_today_view.py` exists to close
(`feedback_brief_act_count_source`).
"""

from __future__ import annotations

from stock_analyzer.decision_bucket import bucket_act_by_type

# EXIT -> TRIM -> WATCH, matching Summary's own chip-row ordering (worst
# first). `counts` keys straight off decision_bucket.bucket_act_by_type's
# own return shape -- no parallel copy of that canon lives here.
_CHIP_ORDER = ("EXIT", "TRIM", "WATCH")

# The only `state` values act_today_view() can ever actually return. Anything
# else (a typo'd state string, a future schema change this module hasn't
# learned about yet) must fail loud to "offline", never calm.
_RECOGNIZED_STATES = {"offline", "clear", "act"}


def _offline_shape() -> dict:
    return {"state": "offline", "n": None, "chips": {}, "n_resolved": 0}


def _n_resolved(view: dict) -> int:
    # Explicit type check rather than `view.get("resolved") or []` —
    # OFFLINE_SENTINEL_COLLAPSE is a recurring bug-class this repo's own
    # antipattern gate polices; a non-list here is malformed input, not a
    # legitimate offline sentinel, but the safe reading is identical either
    # way: count nothing rather than raise.
    resolved = view.get("resolved")
    return len(resolved) if isinstance(resolved, list) else 0


def _act_shape(view: dict) -> dict:
    counts = bucket_act_by_type(view["active"]).get("counts")
    if not isinstance(counts, dict):
        counts = {}
    chips = {k: counts[k] for k in _CHIP_ORDER if counts.get(k)}
    return {
        "state": "act",
        # Trust the already-computed count -- never recompute from
        # len(active). If the two ever disagreed that would itself be a bug
        # worth a test catching, not something to silently reconcile here.
        "n": view.get("n_active"),
        "chips": chips,
        "n_resolved": _n_resolved(view),
    }


def act_pointer(view: dict | None) -> dict:
    """Return `{"state", "n", "chips", "n_resolved"}` for Home's top pointer.

    `view` is the dict `act_today_view()` returned THIS render (never a
    second call). Never raises.

    Resolution order (deliberately NOT independent branches -- the second
    rule can override the first and third):
    1. `view` isn't a dict, or its `state` isn't one of the three recognized
       values -> `"offline"`, `n=None`. Fail loud, never calm.
    2. A non-empty `active` list -> ALWAYS `"act"`, regardless of what
       `view["state"]` itself claims -- defense-in-depth against a future
       inconsistency between the two; presence of active items is ground
       truth.
    3. `state == "offline"` -> offline shape.
    4. Otherwise (clear) -> `n=0`, `chips={}`, `n_resolved` from `resolved`.
    """
    if not isinstance(view, dict) or view.get("state") not in _RECOGNIZED_STATES:
        return _offline_shape()

    active = view.get("active")
    if isinstance(active, list) and active:
        return _act_shape(view)

    if view.get("state") == "offline":
        return _offline_shape()

    return {
        "state": "clear",
        "n": 0,
        "chips": {},
        "n_resolved": _n_resolved(view),
    }
