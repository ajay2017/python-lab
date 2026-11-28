"""A partly priced book holds new buys in the unpriced holding's sector (buy-check C5,
chunk A; docs/plans/entry-fail-closed-c5-spec.md, owner QA1-QA4 2026-11-27).

When a holding can't be priced it drops out of the portfolio frame, so its sector's
weight reads LOW and the sector cap (G-05 on the Watchlist, G-16 on Grow Today) can't
be trusted for any new buy into that sector. This module is the ONE decision and the
wording every surface reads:

  decide(sector, unpriced, *, lane)  hold dict for a candidate in `sector`, else None
  page_line(unpriced)                the Watchlist page's one amber line
  email_line(fit_state)              the one line in the two cron emails
  analysis_tile(sector, unpriced)    the Analysis "Sector cap" tile's (None, detail)

`unpriced` is `buy_check_state.unpriced_holdings` / `fit_state["unpriced"]`:
{TICKER: curated sector | UNPRICED_ALL}. A holding whose sector the app can't name
(every ETF, any uncurated stock) reads `UNPRICED_ALL` and holds every buy the sector
cap covers (QA1). A candidate with no classified sector is already held by
`sector_unknown` and is not held again here. ETF picks never meet the sector cap and
are never held by this check.

THE HOLD IS AN ABSTENTION. It sits after every policy gate (a real G-05 / G-16 breach
binds first, with its ledger row); it has no gate id and writes no ledger row. A breach
in the unpriced sector can only be higher in truth; other sectors read slightly high
(conservative). No clock.

Pure: no streamlit, no db, no clock; inputs are never mutated; nothing here raises.
No dollar signs or bold markers in any string it builds (the renderers interpolate
them as markdown / HTML).
"""
from __future__ import annotations

from stock_analyzer.buy_check_state import UNPRICED_ALL
from stock_analyzer.constants import (
    BRIEF_AUTO_REFRESH_MINUTES, SECTOR_CEILING, UNCLASSIFIED_SECTOR,
)

CHECK = "fit_unpriced"

_LANES = ("watchlist", "pick", "add", "analysis")


def _clean_map(unpriced) -> dict:
    """{UPPER ticker: sector str} for a usable `unpriced` map, else {}."""
    if not isinstance(unpriced, dict):
        return {}
    out: dict = {}
    for tk, sec in unpriced.items():
        t = str(tk).strip().upper()
        if t:
            out[t] = str(sec)
    return out


def _join(tickers) -> str:
    """"A", "A and B", "A, B and C"."""
    t = list(tickers)
    if len(t) <= 1:
        return "".join(t)
    return ", ".join(t[:-1]) + " and " + t[-1]


def _cap() -> str:
    return f"{SECTOR_CEILING:.0f}"


def decide(sector, unpriced, *, lane: str = "watchlist") -> "dict | None":
    """The hold for a candidate whose classified sector is `sector`, or None.

    Held when an unpriced holding sits in the same sector, or under `UNPRICED_ALL`
    (a holding whose sector isn't known) for any classified sector. None when
    `unpriced` is empty / unreadable (fit_state decides unknown separately), or when
    `sector` is blank / None / the unclassified bucket. `lane` ("watchlist" /
    "pick" / "add" / "analysis") words only the clear condition.

    Returns {"check": "fit_unpriced", "sector", "tickers", "all", "reason",
    "clears_when", "gate_id": None, "gate_value": None, "gate_threshold": None}.
    Never raises."""
    try:
        sec = str(sector).strip() if sector is not None else ""
        if not sec or sec == UNCLASSIFIED_SECTOR or sec == UNPRICED_ALL:
            return None
        m = _clean_map(unpriced)
        if not m:
            return None
        all_t = sorted(t for t, s in m.items() if s == UNPRICED_ALL)
        same_t = sorted(t for t, s in m.items() if s == sec)
        if all_t:
            # A1 review: the clear condition names every holding that still holds
            # this sector (the unknown-sector ones and any in this sector).
            tickers, is_all = all_t, True
            clear_t = sorted(set(all_t) | set(same_t))
        elif same_t:
            tickers, is_all = same_t, False
            clear_t = same_t
        else:
            return None
        names = _join(tickers)
        one = len(tickers) == 1
        if is_all:
            reason = (
                f"{names} couldn't be priced and "
                f"{'its sector isn' if one else 'their sectors aren'}'t in the app's "
                f"sector map, so any sector's weight may read low and the {_cap()}% "
                f"sector cap can't be checked"
            )
        else:
            reason = (
                f"{names} couldn't be priced, so "
                f"{'it is' if one else 'they are'} missing from your {sec} weight "
                f"and the {_cap()}% sector cap can't be checked for this name"
            )
        clears = (f"{_join(clear_t)} can be priced again — re-checked at the next scan "
                  f"or page load")
        if lane in ("pick", "add"):
            clears += (f" (🏠 Home rebuilds on its own every {BRIEF_AUTO_REFRESH_MINUTES} "
                       f"minutes in pre-market and market hours)")
        return {
            "check": CHECK, "sector": sec, "tickers": list(clear_t), "all": is_all,
            "reason": reason, "clears_when": clears,
            "gate_id": None, "gate_value": None, "gate_threshold": None,
        }
    except Exception:
        return None


def _groups(unpriced) -> "tuple[list, dict]":
    """(sorted tickers whose sector is unknown, {sector: sorted tickers})."""
    m = _clean_map(unpriced)
    all_t = sorted(t for t, s in m.items() if s == UNPRICED_ALL)
    by_sector: dict = {}
    for t, s in m.items():
        if s != UNPRICED_ALL:
            by_sector.setdefault(s, []).append(t)
    for s in by_sector:
        by_sector[s].sort()
    return all_t, by_sector


def page_line(unpriced) -> "str | None":
    """The Watchlist page's amber line for a partly priced book, or None when every
    holding was priced (or `unpriced` couldn't be read)."""
    try:
        all_t, by_sector = _groups(unpriced)
        if all_t:
            one = len(all_t) == 1
            every = sorted(set(all_t) | {t for ts in by_sector.values() for t in ts})
            until = (f"{'it can' if one else 'they can'}" if every == all_t
                     else f"{_join(every)} can")
            return (f"⚠️ {_join(all_t)} couldn't be priced and "
                    f"{'its sector isn' if one else 'their sectors aren'}'t in the app's "
                    f"sector map, so Enter Now is held for every name the sector cap "
                    f"covers until {until} be priced.")
        if not by_sector:
            return None
        parts, n_all = [], 0
        for sec in sorted(by_sector):
            t = by_sector[sec]
            n_all += len(t)
            parts.append(f"{_join(t)} couldn't be priced, so "
                         f"{'it is' if len(t) == 1 else 'they are'} missing from your {sec} weight")
        secs = _join(sorted(by_sector))
        return (f"⚠️ {'; '.join(parts)}. Enter Now is held for {secs} names until "
                f"{'it can' if n_all == 1 else 'they can'} be priced.")
    except Exception:
        return None


def email_line(fit_state) -> "str | None":
    """The one line the Watchlist and pick emails carry when part of the book
    couldn't be priced this run, or None. Reads `fit_state["unpriced"]` of a ready
    state only."""
    try:
        if not isinstance(fit_state, dict) or fit_state.get("state") != "ready":
            return None
        all_t, by_sector = _groups(fit_state.get("unpriced"))
        if all_t:
            one = len(all_t) == 1
            every = sorted(set(all_t) | {t for ts in by_sector.values() for t in ts})
            return (f"Part of your book couldn't be priced this run ({', '.join(every)}): "
                    f"{'its sector isn' if one else 'their sectors aren'}'t known, so every "
                    f"name the sector check covers is held.")
        if not by_sector:
            return None
        names = sorted(t for ts in by_sector.values() for t in ts)
        return (f"Part of your book couldn't be priced this run ({', '.join(names)}): "
                f"names in {_join(sorted(by_sector))} are held, and the sector check "
                f"ran on the rest.")
    except Exception:
        return None


def analysis_tile(sector, unpriced, current_ok=None) -> "tuple | None":
    """(None, detail) for the Analysis "Sector cap" tile when an unpriced holding
    makes this sector's weight incomplete (the tile reads "—", not a false pass), else
    None. Display only: the size is unchanged (owner QA2).

    A tile that already FAILS (`current_ok is False`: a measured breach) is never
    downgraded: an unpriced holding in the same sector can only make the true weight
    higher, so the ✗ stays (A2 review; a real breach binds first)."""
    try:
        if current_ok is False:
            return None
        d = decide(sector, unpriced, lane="analysis")
        if d is None:
            return None
        return (None, f"Weight incomplete ({', '.join(d['tickers'])} unpriced)")
    except Exception:
        return None
