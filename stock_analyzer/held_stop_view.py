"""Display helpers for the held-position stop (finding #26 C3): the "How your
stop is set" explainer rows and the what-if simulator. Pure; reads only a
result dict from `held_stop_wiring.build_held_stops`. Not a gate: nothing here
decides a breach or a stop level; `what_if` only restates the rule for a
hypothetical next close.
"""
from __future__ import annotations

import math
from datetime import date

from stock_analyzer import constants as _C
from stock_analyzer.data import is_trading_day
from stock_analyzer.held_stop import _tier


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _trail_from(since) -> str:
    """ISO date the trailing / profit-lock window starts: the later of the
    position's start and HELD_TRAIL_START (the stop's go-live)."""
    go = date.fromisoformat(str(_C.HELD_TRAIL_START))
    try:
        d = since if isinstance(since, date) else date.fromisoformat(str(since)[:10])
        return max(d, go).isoformat()
    except (TypeError, ValueError):
        return go.isoformat()


def from_row(row) -> dict:
    """The held-stop dict (as `build_held_stops` returns it) rebuilt from a
    `build_portfolio_df` row's "Auto Stop" / "Stop Basis" / "Stop ..." columns."""
    def g(k):
        try:
            v = row[k]
        except (KeyError, IndexError, TypeError):
            return None
        return None if (isinstance(v, float) and v != v) else v
    auto = _f(g("Auto Stop"))
    label = g("Stop Type Auto")
    known = {lab for _, _, lab in _C.STOP_RATCHET_LEVELS}
    return {
        "stop": auto,
        "basis": g("Stop Basis") or ("unavailable" if auto is None else None),
        "label": label,
        "note": g("Stop Note") or "",
        "since": g("Stop Since"),
        "atr_last": _f(g("Stop ATR")),
        "hw_close": _f(g("Stop HW Close")),
        "ratchet_label": label if label in known else None,
        "components": {"entry": _f(g("Stop Entry")), "trailing": _f(g("Stop Trailing")),
                       "ratchet": _f(g("Stop Ratchet"))},
    }


def basis_caption(row) -> str | None:
    """Caption for the Analysis Stop Loss tile of a held name: a ⚠️ line when the
    automatic stop is not on a full basis (partial / prior_session / unavailable),
    an ℹ️ line on a buy day, None on a full basis or when `row` carries no
    held-stop columns."""
    h = from_row(row)
    basis = h.get("basis")
    if basis == "buy_day" and h.get("note"):
        return f"ℹ️ {h['note']}"
    if basis in ("partial", "prior_session", "unavailable") and h.get("note"):
        try:
            manual = str(row["Stop Type"]) == "Manual"
        except (KeyError, IndexError, TypeError):
            manual = False
        if basis == "unavailable" and manual:
            return "⚠️ No automatic stop could be built; your manual stop applies."
        return f"⚠️ {h['note']}"
    return None


def component_rows(h: dict) -> list[dict]:
    """[{"kind", "label", "value", "text"}] for each stop component that exists,
    highest first. `text` is plain English and only states what the numbers are."""
    h = h if isinstance(h, dict) else {}
    comps = h.get("components")
    comps = comps if isinstance(comps, dict) else {}
    rows = []
    e = _f(comps.get("entry"))
    if e is not None:
        rows.append({"kind": "entry", "label": "Entry Stop", "value": round(e, 2),
                     "text": f"Your first fill minus {_C.ATR_STOP_MULT:g} x ATR on the last "
                             "completed day before you bought."})
    t = _f(comps.get("trailing"))
    if t is not None:
        rows.append({"kind": "trailing", "label": "Trailing Stop", "value": round(t, 2),
                     "text": "The highest of each day's close minus "
                             f"{_C.HELD_TRAIL_ATR_MULT:g} x that day's ATR, over completed days "
                             f"since {_trail_from(h.get('since'))}."})
    r = _f(comps.get("ratchet"))
    if r is not None:
        rows.append({"kind": "ratchet", "label": str(h.get("ratchet_label") or "Profit Lock"),
                     "value": round(r, 2),
                     "text": "A floor above your average cost, set by the best completed close "
                             f"since your last buy or {_C.HELD_TRAIL_START}, whichever is later; "
                             "it stays after the price falls back."})
    rows.sort(key=lambda x: -x["value"])
    return rows


def driver_label(w: dict) -> str:
    """Which component of a `what_if` result binds, as a label."""
    comps = w.get("components") if isinstance(w, dict) else None
    comps = comps if isinstance(comps, dict) else {}
    vals = {k: v for k, v in comps.items() if v is not None}
    if not vals:
        return "Stop"
    best = max(vals.values())
    if vals.get("ratchet") == best:
        return str(w.get("ratchet_label") or "Profit Lock")
    if vals.get("trailing") == best:
        return "Trailing Stop"
    return "Entry Stop"


def what_if(h: dict, price: float, avg_cost: float) -> dict | None:
    """The stop for the NEXT session if a session closed at `price` (ATR held at
    its last value, as the existing what-if does). BY CONSTRUCTION never below
    the current Auto Stop: the result is max(current stop, the recomputed
    components). None when `h` has no usable stop / ATR or the inputs are bad
    (a prior_session or unavailable basis has no components to extend)."""
    h = h if isinstance(h, dict) else {}
    cur, atr, px, cost = _f(h.get("stop")), _f(h.get("atr_last")), _f(price), _f(avg_cost)
    if cur is None or atr is None or px is None or px <= 0 or cost is None or cost <= 0:
        return None
    if h.get("basis") in ("prior_session", "unavailable", "buy_day"):
        return None
    comps = h.get("components")
    comps = dict(comps) if isinstance(comps, dict) else {}
    trailing = max([v for v in (_f(comps.get("trailing")), px - float(_C.HELD_TRAIL_ATR_MULT) * atr)
                    if v is not None])
    hw = max(v for v in (_f(h.get("hw_close")), px) if v is not None)
    ratchet = _f(comps.get("ratchet"))
    label = h.get("ratchet_label")
    tier = _tier((hw - cost) / cost * 100.0, _C.STOP_RATCHET_LEVELS)
    if tier is not None:
        new_r = cost * (1 + tier[0])
        if ratchet is None or new_r > ratchet:
            ratchet, label = new_r, tier[1]
    entry = _f(comps.get("entry"))
    best = max(v for v in (entry, trailing, ratchet) if v is not None)
    stop = max(cur, round(best, 2))
    return {"stop": stop, "current": cur, "raised": stop > cur,
            "components": {"entry": entry, "trailing": trailing, "ratchet": ratchet},
            "ratchet_label": label, "price": px}


# ── one-time "stops recalculated" notice (owner decision D4, 2026-10-30) ─────
def sessions_since_go_live(today: date) -> int | None:
    """Trading sessions in (HELD_TRAIL_START, today], today included; None before
    go-live (today <= HELD_TRAIL_START). HELD_TRAIL_START is the last session
    BEFORE go-live, so the weekend after the deploy counts 0."""
    start = date.fromisoformat(str(_C.HELD_TRAIL_START))
    if today <= start:
        return None
    n, d = 0, start
    while d < today:
        d = date.fromordinal(d.toordinal() + 1)
        if is_trading_day(d):
            n += 1
    return n


def notice_visible(today: date) -> bool:
    """True from the deploy (the day after HELD_TRAIL_START) through
    HELD_STOP_NOTICE_SESSIONS trading sessions after it."""
    n = sessions_since_go_live(today)
    return n is not None and n <= int(_C.HELD_STOP_NOTICE_SESSIONS)


def recalc_notice(today: date) -> str | None:
    """The Home notice text, or None outside the window."""
    if not notice_visible(today):
        return None
    return (
        "ℹ️ Your automatic stops were recalculated. Each one is now built from "
        "completed trading days only, with a trailing part (the highest of each "
        f"day's close minus {_C.HELD_TRAIL_ATR_MULT:g} x that day's ATR) that only "
        "moves up while the price history is complete. Because of this, a name "
        "whose price is already below its new stop shows a sell-all. This notice "
        f"shows for {int(_C.HELD_STOP_NOTICE_SESSIONS)} trading sessions."
    )


def email_notice_line(today: date) -> str | None:
    """One plain line for the protective email inside the same window."""
    if not notice_visible(today):
        return None
    return ("Stops were recalculated with a trailing rule that only moves up while "
            "price history is complete, so some names may show a sell-all today.")


def neg_cache_active(failed_at, now) -> bool:
    """True while a failed long-history fetch (at epoch seconds `failed_at`) is
    still inside HELD_STOP_UNADJ_NEG_CACHE_SEC, so the app doesn't retry it."""
    try:
        return failed_at is not None and (float(now) - float(failed_at)) < float(
            _C.HELD_STOP_UNADJ_NEG_CACHE_SEC)
    except (TypeError, ValueError):
        return False


def held_exit_stop_caption(row, price) -> str | None:
    """Caption for the Exit Plan of a HELD name: the held stop in force (named by
    its own label), or the breach and what the Brief says. Uses the app's own
    breach rule (`is_stop_breached`, rounded gap <= 0), never the bundle's
    fresh-entry stop. None when `price` is unusable. `$` is escaped for markdown."""
    from stock_analyzer.act_today_precedence import is_stop_breached
    from stock_analyzer.stop_breach_text import stop_noun
    px = _f(price)
    if px is None or px <= 0:
        return None

    def g(k):
        try:
            return row[k]
        except (KeyError, IndexError, TypeError):
            return None
    stop = _f(g("Stop"))
    if stop is None or stop <= 0:
        return "⚠ No stop is available for this position — set one at your broker."
    noun = stop_noun(g("Stop Type"))
    gap_missing = _f(g("Gap to Stop (%)")) is None
    if is_stop_breached(px, stop, gap_missing=gap_missing):
        _where = "below" if px < stop else "at or just above"
        return (f"⛔ Your {noun} is breached: price \\${px:.2f} is {_where} your {noun} "
                f"\\${stop:.2f}. Today's Brief says sell all at the next open.")
    return (f"📉 Your {noun}: \\${stop:.2f} — if the price breaks below it, Today's Brief calls a full exit at the next open.")


def held_row(port_df, ticker) -> dict | None:
    """The `build_portfolio_df` row (as a dict) for a HELD ticker, else None."""
    try:
        if port_df is None or port_df.empty or "Ticker" not in port_df.columns:
            return None
        m = port_df[port_df["Ticker"].astype(str).str.strip().str.upper()
                    == str(ticker).strip().upper()]
        return None if m.empty else m.iloc[0].to_dict()
    except Exception:
        return None


def display_stop(bundle_stop, holding) -> float | None:
    """The stop to DRAW / list for a ticker: for a held name its held stop (None
    when it has none; never the bundle's fresh-entry stop), else the bundle stop."""
    if holding is None:
        x = _f(bundle_stop)
        return x if x is not None and x > 0 else None
    try:
        x = _f(holding["Stop"])
    except (KeyError, IndexError, TypeError):
        return None
    return x if x is not None and x > 0 else None
