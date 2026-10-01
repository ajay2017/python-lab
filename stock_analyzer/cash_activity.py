"""Pure helpers for 💰 Account → Cash Activity (display only).

The chart used to plot every income event as a positive bar, grouped only by
`event_type`. So a margin-interest charge looked like income, and a Gold Plan
credit was netted into that month's interest: June 2026 showed $7.08 when the
owner had actually paid $36.59 against a $29.45 credit. Found from a live
screenshot on 2026-10-01. These helpers keep charges and credits apart and
signed, and line up the zero of the bar axis with the zero of the realized-P&L
axis so "below the line" means the same thing on both.

Awareness only: nothing here feeds account_flows, Modified Dietz, or any gate.
"""
from __future__ import annotations

import math

# Display order, bottom-to-top within a month's stack.
CATEGORIES = ("Dividend", "Interest earned", "Margin interest", "Fee")


def chart_category(ev: dict) -> "str | None":
    """Bar category for one income event, or None when it can't be placed.

    Interest is split by SIGN, using the same convention capital_vs_margin
    confirmed against the real statement (2026-09-11): negative = margin
    interest charged (MINT), positive = earned (GMPC / INT). Zero/NaN amounts
    return None because they are neither a charge nor a credit."""
    try:
        amt = float(ev.get("amount"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amt) or amt == 0:
        return None
    et = str(ev.get("event_type") or "").strip().lower()
    if et == "dividend":
        return "Dividend"
    if et == "interest":
        return "Margin interest" if amt < 0 else "Interest earned"
    if et == "fee":
        return "Fee"
    return None


def unconfirmed_broker_fees(events: "list[dict]") -> "list[dict]":
    """Broker-synced, ticker-less raw_code='FEE' rows in `events`.

    Pass the list AFTER broker_sync.dedupe_income_events. Any such row still
    present has no matching statement row. On this account the broker sync
    reports margin interest as FEE (confirmed 2026-09-12, 3-for-3 against the
    statement's MINT rows). But a genuine fee such as the Gold annual charge
    could also arrive as FEE, so the row is NOT reclassified here. The page
    only says it may be margin interest; importing that month's statement
    resolves it, because the dedup keeps the statement's MINT row. Pure."""
    out = []
    for ev in events or []:
        if str(ev.get("snaptrade_txn_id") or "").startswith("csv:"):
            continue
        if str(ev.get("raw_code") or "").strip().upper() != "FEE":
            continue
        if str(ev.get("ticker") or "").strip():
            continue
        out.append(ev)
    return out


def _finite(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def zero_aligned_ranges(a_lo, a_hi, b_lo, b_hi, pad: float = 0.08) -> "tuple[tuple[float, float], tuple[float, float]]":
    """Axis ranges for two y-axes (primary a, secondary b) whose zero lines sit
    at the same height. Each axis keeps its own data extent, padded by `pad`
    of its span, and only ever EXTENDS downward to match the other axis's
    zero position, so no data is clipped. An axis with no data gets (-1, 1).
    Non-finite inputs are treated as 0. Pure."""
    def _padded(lo, hi):
        lo, hi = min(_finite(lo), 0.0), max(_finite(hi), 0.0)
        span = hi - lo
        if span <= 0:
            return -1.0, 1.0
        return lo - pad * span, hi + pad * span

    (alo, ahi), (blo, bhi) = _padded(a_lo, a_hi), _padded(b_lo, b_hi)
    # Fraction of the axis height that sits below zero; strictly in (0, 1)
    # because padding guarantees lo < 0 < hi.
    z = max(-alo / (ahi - alo), -blo / (bhi - blo))
    k = z / (1.0 - z)
    return (-k * ahi, ahi), (-k * bhi, bhi)
