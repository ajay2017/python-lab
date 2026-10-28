"""Pure wording for the collapsed 🩺 System Trust ② Data stores row.

Built only from what the probe actually checked: an "ok" store with
`age_checked` was graded against its expected date; an "ok" store without it
only exists (its age isn't graded here); an unconditional daily whose expected
date couldn't be computed is bucketed separately; anything else is unconfirmed
and named. No streamlit, no I/O.
"""
from __future__ import annotations

_MAX_NAMED = 5

_REASONS = {
    "empty": "exists, no rows yet",
    "error": "couldn't be read",
    "offline": "database unreachable",
    "present": "latest date unreadable",
}


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def stores_summary(rows: list) -> str:
    """One sentence for a collapsed (no warn/down) data-stores section."""
    if not rows:
        return "No data stores were probed this run."
    fresh = exist_only = daily_unchecked = 0
    unconfirmed: list[tuple[str, str]] = []
    for r in rows:
        if r.get("severity") == "ok":
            if r.get("age_checked"):
                fresh += 1
            elif r.get("unconditional"):
                daily_unchecked += 1
            else:
                exist_only += 1
        else:
            unconfirmed.append((
                str(r.get("label") or r.get("table") or "unnamed store"),
                _REASONS.get(r.get("state"), "not confirmed"),
            ))

    parts: list[str] = []
    if fresh:
        parts.append(f"{fresh} daily {_plural(fresh, 'store is', 'stores are')} fresh")
    if daily_unchecked:
        parts.append(
            f"{daily_unchecked} daily {_plural(daily_unchecked, 'store exists but its', 'stores exist but their')} "
            "freshness couldn't be checked this run"
        )
    if exist_only:
        parts.append(
            f"{exist_only} {_plural(exist_only, 'other exists', 'others exist')} "
            f"(existence-only check, so {_plural(exist_only, 'its', 'their')} age isn't graded here)"
        )
    if unconfirmed:
        txt = ", ".join(f"{lbl}: {why}" for lbl, why in unconfirmed[:_MAX_NAMED])
        extra = len(unconfirmed) - _MAX_NAMED
        if extra > 0:
            txt += f" and {extra} more"
        parts.append(f"not confirmed: {txt}")
    lead = "No store found missing —" if daily_unchecked else "No store found missing or stale —"
    return f"{lead} " + "; ".join(parts) + "."
