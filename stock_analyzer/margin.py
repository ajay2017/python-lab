"""
Margin maintenance awareness computations.

Pure functions only — no Streamlit, no DB calls, no side effects.
All results are awareness-only; nothing here gates a trade or recommendation.
"""
from __future__ import annotations


def call_distance(
    stock_value: float,
    owner_equity: float,
    margin_debit: float,
    rate: float,
) -> dict | None:
    """Return margin call-distance metrics, or None when not applicable.

    Parameters
    ----------
    stock_value : total market value of holdings (equity + debit)
    owner_equity : capital the owner actually holds (stock_value - debit)
    margin_debit : the broker loan amount (positive number)
    rate : maintenance rate as a decimal, e.g. 0.25

    Returns None when:
    - no margin debit (debit <= 0) — panel should hide
    - stock_value <= 0 — nothing to compute
    - rate >= 1 — degenerate denominator

    The call-distance formula accounts for the maintenance floor declining
    with the book (rate is applied to live market value, not a fixed dollar):

        call fires when  market_value < debit / (1 - rate)
        so the decline needed:
        call_distance_pct = -(cushion / (stock_value * (1 - rate))) * 100

    where cushion = owner_equity - stock_value * rate
    """
    if margin_debit <= 0 or stock_value <= 0 or rate >= 1:
        return None

    maintenance_req = stock_value * rate
    cushion = owner_equity - maintenance_req
    denominator = stock_value * (1.0 - rate)
    call_distance_pct = -(cushion / denominator) * 100.0
    in_call = cushion <= 0

    return {
        "maintenance_req": maintenance_req,
        "cushion": cushion,
        "call_distance_pct": call_distance_pct,
        "in_call": in_call,
    }
