"""
Margin maintenance awareness computations.

Pure functions only — no Streamlit, no DB calls, no side effects.
All results are awareness-only; nothing here gates a trade or recommendation.

Exception (F-255, 2026-08-25): `resolve_net_capital` and `held_over_capital_cap`
below feed the NEW-POSITION capital-basis sizing cap (NET_CAPITAL_POSITION_CAP_PCT,
stock_analyzer.risk.position_sizing / sizing_unavailable_reason). That cap is a
real, additive sizing constraint — the module-level "awareness only" claim above
still holds for `call_distance`/`capital_basis_weight` and the account-page
panel, but is no longer true of every function in this file. risk.py itself
does NOT import this module (see tests/test_margin.py's gate-module
allowlist) — it re-derives the same percentage inline rather than importing
capital_basis_weight, to keep this module out of the decision-engine import
graph even though one policy decision (the 25%-of-net-capital cap) now
consumes numbers this module computes.
"""
from __future__ import annotations

import pandas as pd


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


def capital_basis_weight(market_value: float, net_capital: float) -> float | None:
    """Weight of `market_value` as a percentage of the owner's actual capital.

    Awareness-only dual-basis readout (2026-08-24): every concentration gate
    (SINGLE_NAME_CEILING, SECTOR_CEILING) computes weight against gross
    holdings value, which equals owner capital only when unlevered. At real
    leverage, "15% of book" understates what a position is worth against the
    capital the owner actually holds. This does not change any gate's input —
    it exists purely to show the second number alongside the first.

    Returns None when net_capital <= 0 (no capital to weigh against — e.g. a
    margin-called or net-negative account, where a percentage is meaningless).
    """
    if net_capital <= 0:
        return None
    return market_value / net_capital * 100.0


def resolve_net_capital(
    gross_book: float,
    account_cash_rec: dict | None,
    stale_days_limit: int,
    now,
) -> tuple[float | None, str]:
    """Net capital (equity after margin debit) for the F-255 capital-basis cap.

    Parameters
    ----------
    gross_book : total market value of held positions — the same gross-book
        figure SINGLE_NAME_CEILING/SECTOR_CEILING already gate on.
    account_cash_rec : the manually-entered account-cash record (the same
        shape the Account page's margin-awareness panel reads), or None if
        never entered. Expected keys: "cash_balance" (signed net cash;
        negative = a margin debit) and "updated_at" (freshness timestamp).
    stale_days_limit : age in days beyond which a debit figure is untrusted
        (pass ACCOUNT_CASH_STALE_DAYS — this function takes it as a
        parameter rather than importing constants itself, staying pure).
    now : a tz-aware "current time" passed in by the caller (this function
        does no clock reads of its own, so it stays pure/testable).

    Returns ``(net_capital, basis)``:
      (None, "unlevered") — no record, no "updated_at", or cash_balance >= 0
          (no debit to net against gross book). The capital cap is simply
          INERT here — callers should skip it, not treat this as an error.
      (None, "stale")     — a debit exists but the record is older than
          `stale_days_limit` days. Same "unknown degrades to inert" posture
          as concentration.gating_denominator's equity-basis fallback — an
          old manually-entered number must not drive a live sizing cap.
      (net, "levered")    — a fresh debit exists and
          net = gross_book + cash_balance (cash_balance is negative, so this
          subtracts the debit) is positive.
      (net, "called")     — a fresh debit exists and net is <= 0
          (margin-called / net-negative). Deliberately returns the
          non-positive number, NOT None — callers use this to fail CLOSED
          (e.g. held_over_capital_cap treats a non-positive net_capital as
          "can't evaluate" via its own None/<=0 guard, while a caller that
          wants to surface the called state explicitly still has the number).
    """
    if not account_cash_rec or not account_cash_rec.get("updated_at"):
        return None, "unlevered"
    cash_balance = account_cash_rec.get("cash_balance")
    if cash_balance is None or cash_balance >= 0:
        return None, "unlevered"
    age_days = (now - pd.to_datetime(account_cash_rec["updated_at"], utc=True)).days
    if age_days > stale_days_limit:
        return None, "stale"
    net = gross_book + cash_balance
    if net > 0:
        return net, "levered"
    return net, "called"


def shock_call_outcome(
    *,
    stock_value_now: float,
    shocked_stock_value: float,
    margin_debit: float,
    rate: float,
    forced_sale_proceeds: float = 0.0,
    warn_band_pct: float | None = None,
) -> dict | None:
    """Distance to a margin call under a hypothetical price shock, plus the
    forced-sale cascade — composes `call_distance` with a forward_sim
    scenario (2026-09-01, leverage-aware shock modeling).

    This never re-derives the call-distance formula: it calls the existing
    `call_distance` twice (once at the shocked price alone, once again after
    netting a forced sale's proceeds against the debit) so the awareness
    panel (call_distance today) and this scenario replay can never drift
    apart from each other.

    Parameters
    ----------
    stock_value_now : today's UNSHOCKED gross book value. Used to compute
        `cushion_delta_from_now` (today's cushion, via `call_distance` again
        at `stock_value_now` / `stock_value_now - margin_debit`, minus
        `shock_cushion`) — the shocked calculation itself does not need it,
        since `shocked_stock_value` already IS the post-shock state that
        formula needs.
    shocked_stock_value : gross book value AFTER the shock (e.g.
        stress_test.run_scenario's `post_shock_value`).
    margin_debit : today's broker loan (positive number) — held fixed
        through the first-order shock; a price move doesn't change what's
        owed.
    rate : maintenance rate as a decimal, e.g. 0.25. Pass
        constants.MARGIN_MAINTENANCE_RATE.
    forced_sale_proceeds : dollars of the shocked book already sold off by
        the app's OWN mechanical rules in the scenario being modelled (e.g.
        forward_sim's `survivors[tier]["proceeds"]`) and used to pay down
        the debit. Defaults to 0.0 (no cascade modelled).
    warn_band_pct : the same yardstick `summary_view.book_safety` compares
        against (pass constants.FRAGILITY_PULLBACK_PCT) — inclusive, same
        `<=` convention. None disables the "warn" tier entirely; it must
        never be invented here.

    Returns None when: `margin_debit <= 0`, `shocked_stock_value <= 0`, or
    `rate >= 1` — the same "not applicable" guard as `call_distance` — a
    book with no margin debit has nothing to model here.

    Returns a dict:
      shock_cushion           : $ cushion at the shocked price, no sales
      shock_call_distance_pct : % further decline from the SHOCK to the call
                                (call_distance's own sign convention —
                                negative = distance remaining, positive =
                                already breached)
      shock_in_call           : bool — cushion <= 0 (inclusive)
      shock_tier              : "call" | "warn" | "clear"
      post_sale_cushion       : $ cushion AFTER netting forced_sale_proceeds
                                against the debit. Always >= shock_cushion
                                for 0 <= proceeds <= margin_debit — forced
                                selling can only help the cushion, never
                                worsen it (post_sale_cushion = shock_cushion
                                + forced_sale_proceeds * rate).
      post_sale_in_call       : bool — in-call state after the sale
      call_covered_by_sales   : bool — True only if the shock alone would
                                have triggered a call AND the forced sale
                                fixes it
      debit_after_sales       : max(0, margin_debit - forced_sale_proceeds)
      cushion_delta_from_now  : $ cushion LOST versus today, unshocked
                                (today's cushion, via `call_distance` at
                                `stock_value_now` / `stock_value_now -
                                margin_debit`, minus `shock_cushion`) — a
                                positive number is cushion given up by this
                                shock, a negative number means the shock
                                would (unusually) leave MORE cushion than
                                today. None when `stock_value_now` itself
                                doesn't resolve a cushion (e.g. <= 0).
    """
    if margin_debit <= 0 or shocked_stock_value <= 0 or rate >= 1:
        return None

    shocked_equity = shocked_stock_value - margin_debit
    first = call_distance(shocked_stock_value, shocked_equity, margin_debit, rate)
    if first is None:
        return None
    shock_cushion = first["cushion"]
    shock_call_distance_pct = first["call_distance_pct"]
    shock_in_call = first["in_call"]

    if shock_in_call:
        shock_tier = "call"
    elif warn_band_pct is not None and abs(shock_call_distance_pct) <= abs(warn_band_pct):
        shock_tier = "warn"
    else:
        shock_tier = "clear"

    debit_after_sales = max(0.0, margin_debit - forced_sale_proceeds)
    if debit_after_sales <= 0:
        # The loan is fully repaid by the sale. Equity is invariant to a
        # sale (selling an asset and using the proceeds to pay down the
        # matching debit moves stock_value and debit by the same amount),
        # so the closed form below is exact — and `call_distance` would
        # reject a non-positive debit anyway, so this is handled explicitly
        # rather than letting that guard fire unexpectedly.
        post_sale_cushion = shock_cushion + forced_sale_proceeds * rate
        post_sale_in_call = False
        call_covered_by_sales = shock_in_call
    else:
        # Maintenance requirement is rate x REMAINING stock value, so the
        # sold-off proceeds must come out of stock_value too, not just the
        # debit -- equity (shocked_equity) itself does not change.
        second = call_distance(
            shocked_stock_value - forced_sale_proceeds, shocked_equity,
            debit_after_sales, rate,
        )
        if second is None:
            # Degenerate post-sale state (e.g. proceeds exceeding the
            # shocked book itself) -- fall back to the first-order reading
            # rather than fabricate a number call_distance itself refused.
            post_sale_cushion = shock_cushion
            post_sale_in_call = shock_in_call
        else:
            post_sale_cushion = second["cushion"]
            post_sale_in_call = second["in_call"]
        call_covered_by_sales = shock_in_call and not post_sale_in_call

    today = call_distance(
        stock_value_now, stock_value_now - margin_debit, margin_debit, rate,
    )
    cushion_delta_from_now = (
        today["cushion"] - shock_cushion if today is not None else None
    )

    return {
        "shock_cushion": shock_cushion,
        "shock_call_distance_pct": shock_call_distance_pct,
        "shock_in_call": shock_in_call,
        "shock_tier": shock_tier,
        "post_sale_cushion": post_sale_cushion,
        "post_sale_in_call": post_sale_in_call,
        "call_covered_by_sales": call_covered_by_sales,
        "debit_after_sales": debit_after_sales,
        "cushion_delta_from_now": cushion_delta_from_now,
    }


def held_over_capital_cap(port_df, net_capital: float | None, cap_pct: float) -> list | None:
    """Held positions whose capital-basis weight exceeds `cap_pct`, or None.

    Returns None when `net_capital` is None or <= 0 — "can't evaluate" (no
    valid capital denominator), NEVER an empty list standing in for unknown.
    An empty list is a real answer distinct from "couldn't check": it means
    net_capital WAS valid and nothing breached it.

    `port_df` must carry "Ticker" and "Market Value" columns (the same
    columns app.py/portfolio.py already use for the gross-book weight read).
    """
    if net_capital is None or net_capital <= 0:
        return None
    breaches = []
    for _, row in port_df.iterrows():
        market_value = row.get("Market Value")
        if market_value is None:
            continue
        capital_pct = capital_basis_weight(float(market_value), net_capital)
        # >= , not > : matches the breach convention already used by the sizing
        # cap (risk.py) and assess_add_concentration (concentration.py), both of
        # which treat "at the ceiling" as a breach, not "over" it.
        if capital_pct is not None and capital_pct >= cap_pct:
            breaches.append({
                "ticker": row.get("Ticker"),
                "market_value": float(market_value),
                "capital_pct": capital_pct,
            })
    return breaches
