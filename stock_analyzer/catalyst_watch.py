"""
Catalyst Watch — forward-looking earnings awareness for names the app tracks.

AWARENESS ONLY. This panel does NOT recommend initiating into earnings — the
earnings-proximity gates still suppress that (buying into a binary event is the
gamble the app's posture avoids). Its job is to remove the BLIND SPOT: a tracked
name (held / watchlist / sector universe) reporting without the user ever seeing
it coming (the PANW case — a cybersecurity leader beat after close and the app
had surfaced nothing about it). Post-print, confirmed moves still surface via
the Movers scan; this is the pre-print heads-up that lets the user make their
OWN call.

Pure logic — no I/O. The caller supplies the earnings rows (from the data
layer's market-wide calendar), the tracked sets, a sector lookup, and which
sectors are currently leading. Returns rows ready to render, sorted soonest-first.
"""

from datetime import date, datetime


def _parse_date(s) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _when_label(when: str) -> str:
    """Normalise FMP's time hint into a readable label."""
    w = (when or "").lower()
    if w in ("bmo", "before market open", "pre", "premarket"):
        return "before open"
    if w in ("amc", "after market close", "post", "aftermarket"):
        return "after close"
    return ""


def build_catalyst_watch(
    tracked: set,
    held_tickers: set,
    watchlist: set,
    sector_lookup: dict,
    calendar_rows: list,
    held_earnings: dict,
    leading_sector_names: set,
    today: date,
    window_days: int,
) -> list[dict]:
    """Return upcoming-earnings rows for tracked names within `window_days`.

    tracked              : held ∪ watchlist ∪ sector-universe (the only names we surface)
    sector_lookup        : {ticker: sector label}
    calendar_rows        : [{ticker, date, when}] market-wide (from data.fetch_earnings_calendar)
    held_earnings        : {ticker: 'YYYY-MM-DD'} fallback so held names show even if
                           the market calendar is unavailable (FMP quota/offline)
    leading_sector_names : sectors currently leading (drives the 🔥 context flag)

    Each row: {ticker, date, days, when, sector, sector_hot, ownership}.
    ownership ∈ {"held", "watchlist", "universe"} (held wins, then watchlist).
    De-duplicated by ticker (soonest date kept); sorted soonest-first.
    """
    tracked_u   = {str(t).upper() for t in (tracked or set())}
    held_u      = {str(t).upper() for t in (held_tickers or set())}
    watch_u     = {str(t).upper() for t in (watchlist or set())}
    leading_u   = {str(s) for s in (leading_sector_names or set())}

    # Merge the market calendar (filtered to tracked names) with held fallbacks.
    raw: dict[str, str] = {}   # ticker -> earliest date string
    when_by: dict[str, str] = {}
    for row in (calendar_rows or []):
        t = str(row.get("ticker", "")).upper()
        if t not in tracked_u:
            continue
        d = str(row.get("date", ""))[:10]
        if not d:
            continue
        if t not in raw or d < raw[t]:
            raw[t] = d
            when_by[t] = row.get("when", "")
    # Held fallback — ensures a held name with a known earnings date shows even
    # if the market calendar didn't return it.
    for t, d in (held_earnings or {}).items():
        tu = str(t).upper()
        if tu in held_u and d and (tu not in raw or str(d)[:10] < raw[tu]):
            raw[tu] = str(d)[:10]

    out: list[dict] = []
    for t, dstr in raw.items():
        ed = _parse_date(dstr)
        if ed is None:
            continue
        days = (ed - today).days
        if days < 0 or days > window_days:
            continue
        sector = sector_lookup.get(t, "")
        ownership = "held" if t in held_u else ("watchlist" if t in watch_u else "universe")
        out.append({
            "ticker":     t,
            "date":       dstr,
            "days":       days,
            "when":       _when_label(when_by.get(t, "")),
            "sector":     sector,
            "sector_hot": sector in leading_u if sector else False,
            "ownership":  ownership,
        })

    out.sort(key=lambda r: (r["days"], r["ticker"]))
    return out
