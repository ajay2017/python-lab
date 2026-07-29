"""
Concentration / position-sizing discipline — pure helpers.

Risk is managed more at ENTRY (sizing + diversification) than reactively
(stops/de-risk): a smaller position shrinks every loss at the source. The app
enforced concentration ceilings on *recommendations* but not on the user's
*manual* trades, so a name could grow to 23% of the book with no friction. These
helpers close that:

- `assess_add_concentration` — given a BUY, what % of the book the position
  becomes (single-name + sector) and how many shares to trim back under the
  ceiling. Drives the non-blocking entry-time warning in the Trade Journal.
- `high_beta_share` — the share of the book sitting in high-beta names, a cheap
  honest proxy for correlated-cluster risk ("ten tech names that fall together").

Pure logic — no Streamlit / no I/O / no pandas. Inputs are primitives so the math
is trivially unit-testable.
"""

from __future__ import annotations

import math


def assess_add_concentration(
    *,
    ticker: str,
    add_shares: float,
    price: float,
    existing_name_mv: float,   # current market value held in THIS ticker (0 if new)
    sector_mv: float,          # current market value across the ticker's sector (excl. this name's add)
    portfolio_value: float,    # current total positions value (pre-trade)
    single_ceiling: float,
    sector_ceiling: float,
    sector_elevated: float,
) -> dict | None:
    """Resulting concentration of a BUY, or None if no ceiling is approached.

    The buy is treated as ADDED exposure (post_total = portfolio_value + new_mv) —
    slightly conservative (warns marginally earlier) and correct when the buy is
    new capital; harmless when it's a reshuffle. `suggested_trim_shares` is the
    exact count to remove to bring the single-name weight back to the ceiling
    (accounts for the total shrinking as you trim).
    """
    new_mv = float(add_shares) * float(price)
    if new_mv <= 0 or portfolio_value <= 0 or price <= 0:
        return None

    post_total = portfolio_value + new_mv
    post_name_mv = existing_name_mv + new_mv
    post_name_wt = post_name_mv / post_total * 100.0
    post_sector_mv = sector_mv + new_mv
    post_sector_wt = post_sector_mv / post_total * 100.0

    name_breach = post_name_wt >= single_ceiling
    sector_hard = post_sector_wt >= sector_ceiling
    sector_elev = (not sector_hard) and post_sector_wt >= sector_elevated

    if not (name_breach or sector_hard or sector_elev):
        return None

    # Exact trim to hit the single-name ceiling: solve
    #   (post_name_mv - x·price) / (post_total - x·price) = c   for x (shares).
    trim_shares = 0
    if name_breach:
        c = single_ceiling / 100.0
        denom = price * (1.0 - c)
        if denom > 0:
            x = (post_name_mv - c * post_total) / denom
            trim_shares = max(0, math.ceil(x))

    return {
        "ticker": ticker,
        "post_name_wt": round(post_name_wt, 1),
        "post_sector_wt": round(post_sector_wt, 1),
        "name_breach": name_breach,
        "sector_hard": sector_hard,
        "sector_elevated": sector_elev,
        "single_ceiling": single_ceiling,
        "sector_ceiling": sector_ceiling,
        "sector_elevated_thresh": sector_elevated,
        "suggested_trim_shares": trim_shares,
        "new_mv": round(new_mv, 0),
    }


def gating_denominator(
    equity_value: float,
    account_total: float | None,
    *,
    stale: bool = False,
) -> tuple[float, str]:
    """Denominator for the concentration GATES under the 'tighter-of-both' policy.

    A position's *gate weight* is ``MV / denom``. We pick the denominator that
    makes the gate STRICTER, never looser, relative to the equity-only basis:

    - **Margin** (signed net cash < 0 → ``account_total`` < equity): gate on the
      smaller ``account_total`` (your true net capital). Weights run HIGHER, so
      the 15%/35% ceilings bite sooner — borrowed money amplifies single-name /
      sector risk to the capital you actually own.
    - **Cash on hand** (``account_total`` >= equity): keep the equity
      denominator. Cash must NOT relax a hard suppression — the cash figure is
      manually seeded and can be stale or transient, so only its *sign* (margin)
      is trusted to tighten, never its magnitude to loosen.
    - **Unknown / stale cash** (``account_total`` is None or ``stale``): fall back
      to equity-basis — the gate degrades to today's behaviour rather than firing
      on missing/old data.
    - **Net capital wiped** (``account_total`` <= 0 while levered): no positive
      base to divide by; the book is maximally levered. Floor the denom tiny so
      every name reads over the ceiling (gate suppresses all adds), and return
      basis ``"over-levered"`` so the UI can say so instead of showing nonsense.

    Returns ``(denom, basis)``; basis in {"equity", "account", "over-levered"}.
    Pure — no I/O. ``account_total`` = equity + signed net cash (negative = margin
    debit). See docs/plans/account-baseline.md (gate-basis decision, 2026-06-26).
    """
    eq = float(equity_value or 0.0)
    if account_total is None or stale or eq <= 0:
        return eq, "equity"
    acct = float(account_total)
    if acct >= eq:
        return eq, "equity"               # cash never loosens the gate
    if acct <= 0:
        # Degenerate floor, not a tuned threshold: account_total <= 0 means the
        # account is underwater on margin, so any positive gating denominator
        # is intentionally tiny (1% of equity, floored at $1) to make the
        # concentration gate maximally strict rather than divide-by-zero/negative.
        return max(eq * 0.01, 1.0), "over-levered"
    return acct, "account"                # margin: tighter denominator


def high_beta_share(positions, beta_threshold: float) -> float:
    """Share (%) of the book in high-beta names, over the names with KNOWN beta.

    positions: iterable of (weight_pct, beta). Names with unknown beta (None) are
    excluded from BOTH numerator and denominator — the read is "of the exposure we
    can measure, how much is high-beta," not a fabricated count. Returns 0.0 when
    no beta is known.
    """
    known_w = 0.0
    hi_w = 0.0
    for w, b in positions:
        if w is None or b is None:
            continue
        w = float(w)
        known_w += w
        if float(b) >= beta_threshold:
            hi_w += w
    if known_w <= 0:
        return 0.0
    return round(hi_w / known_w * 100.0, 0)
