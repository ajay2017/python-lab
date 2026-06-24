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
