"""Display snapping for P&L tiles (pure).

A tiny negative remainder (e.g. -0.004) rounds to zero in the string but keeps
its sign ("$-0", "-0.00%") and a red arrow. `signed_display` snaps such a value
to 0.0 so the caller can show a neutral zero.
"""
from __future__ import annotations


def signed_display(value: float, decimals: int) -> tuple[float, bool]:
    """(value, False) unchanged when it survives rounding at `decimals`;
    (0.0, True) when it rounds to zero. Python's round() is half-to-even, so
    -0.5 at 0 dp rounds to -0.0 and snaps to zero (consistent with how the
    f-string formatting of the same value would print "-0")."""
    if round(float(value), decimals) == 0:
        return 0.0, True
    return value, False
