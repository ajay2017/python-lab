"""
Account-level pure calculations (account-baseline v2 — contributions & growth).

Pure functions (no I/O, no Streamlit) so they're trivially testable and shared
by the app + any future broker-sync path. The flow ledger separates external
CONTRIBUTIONS (deposits/withdrawals + an opening baseline) from PERFORMANCE, so
"growth" means money the market made you — not money you deposited.

A flow row is {flow_date, flow_type, amount, note}:
  - 'baseline'   : the contributed-capital anchor at the start of tracking (+).
  - 'deposit'    : external cash IN (+).
  - 'withdrawal' : external cash OUT (-).
`amount` is always stored POSITIVE; the type carries the sign.
"""

from __future__ import annotations

_CONTRIB_TYPES = ("baseline", "deposit")
_WITHDRAW_TYPES = ("withdrawal",)


def net_contributed_capital(flows: list[dict]) -> float:
    """Net contributed capital = baseline + Σ deposits − Σ withdrawals.

    The amount the user has *put in* (net of what they took out). Growth is
    measured against THIS, not against a naive prior value — so a deposit can
    never masquerade as a gain. Unknown/blank types are ignored (never guessed)."""
    ncc = 0.0
    for f in flows or []:
        try:
            amt = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        t = str(f.get("flow_type") or "").strip().lower()
        if t in _CONTRIB_TYPES:
            ncc += amt
        elif t in _WITHDRAW_TYPES:
            ncc -= amt
    return round(ncc, 2)


def account_growth(total_value: float | None, ncc: float) -> dict:
    """Growth of the account vs net contributed capital.

    Returns {"ncc", "growth", "growth_pct"}. growth = total_value − ncc (the
    performance dollars). growth_pct is None when ncc <= 0 (can't take a return
    on zero/negative contributed capital — surfaced as "—" rather than a bogus
    number) or when total_value is unknown (portfolio not loaded)."""
    ncc = round(float(ncc), 2)
    if total_value is None:
        return {"ncc": ncc, "growth": None, "growth_pct": None}
    growth = round(float(total_value) - ncc, 2)
    growth_pct = round(growth / ncc * 100, 2) if ncc > 0 else None
    return {"ncc": ncc, "growth": growth, "growth_pct": growth_pct}


def has_baseline(flows: list[dict]) -> bool:
    """True once a 'baseline' anchor exists — growth is meaningless before it
    (there's no contributed-capital reference to measure against)."""
    return any(str(f.get("flow_type") or "").strip().lower() == "baseline"
               for f in (flows or []))
