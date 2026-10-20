"""S-D — one sizing path for every share count the app hands out.

Pure. No Streamlit, no DB, no clock reads (callers pass `today` / `epoch`).
See docs/plans/one-sizing-path.md.

Owner decision D1 (2026-10-19): SINGLE_NAME_CEILING and
NET_CAPITAL_POSITION_CAP_PCT bind on the WHOLE position after an add (existing
holding value + new shares). This module is the one place a surface asks
"how many shares, and why not" for a held name:

  * `held_value`         -- what the book already holds in a ticker (or None).
  * `size_position`      -- always returns a dict: sized, or no_size with a
                            plain sentence. Never raises; never sizes a held
                            name as a fresh one when the holding is unknown.
  * `held_add_verdict`   -- what Grow Today decided TODAY about adding to this
                            held name (read-only over its raw dict).
  * `concentration_tile` -- "now X% -> after Y%" for the Analysis tile.

Texts here are plain: no markdown and no dollar signs, because they render in
places where a `$...$` pair turns into LaTeX.
"""
from __future__ import annotations

import math

from stock_analyzer import risk
from stock_analyzer.constants import (
    ADD_WINNER_MIN_GAP_PCT,
    COMPOSITE_BUY,
    NET_CAPITAL_POSITION_CAP_PCT,
    RISK_PCT_PER_TRADE,
    SINGLE_NAME_CEILING,
)


def _finite(x) -> bool:
    if isinstance(x, bool):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# ── held_value ────────────────────────────────────────────────────────────────

def held_value(ticker, port_df, holdings_state) -> "float | None":
    """Dollar value already held in `ticker`: 0.0 = not held, None = couldn't check.

    holdings_state: "ready" (holdings loaded), "flat" (definitely holds
    nothing); anything else is unknown. Never raises.
    """
    try:
        known = holdings_state in ("ready", "flat")
        cols = getattr(port_df, "columns", None)
        usable = (port_df is not None and cols is not None and len(port_df) > 0
                  and "Ticker" in cols and "Market Value" in cols)
        if not usable:
            return 0.0 if holdings_state == "flat" else None
        want = str(ticker).strip().upper()
        total = 0.0
        matched = False
        for _, r in port_df.iterrows():
            if str(r["Ticker"]).strip().upper() != want:
                continue
            matched = True
            mv = r["Market Value"]
            if not _finite(mv):
                return None
            total += float(mv)
        if not matched:
            return 0.0 if known else None
        if not math.isfinite(total) or total < 0:
            return None
        return total
    except Exception:
        return None


# ── size_position ─────────────────────────────────────────────────────────────

def _pct(num, den) -> "float | None":
    try:
        if num is None or den is None or not (float(den) > 0):
            return None
        return round(float(num) / float(den) * 100.0, 1)
    except (TypeError, ValueError):
        return None


def _no_size(code, text, existing, gross_book, net_capital) -> dict:
    ex = existing if (existing is not None and _finite(existing)) else None
    return {
        "state": "no_size",
        "code": code,
        "text": text,
        "existing_gross_pct": _pct(ex, gross_book),
        "existing_capital_pct": _pct(ex, net_capital),
    }


def size_position(*, entry, stop, gross_book, net_capital, existing_value,
                  risk_pct=RISK_PCT_PER_TRADE) -> dict:
    """Size an add (or a fresh entry when existing_value == 0). ALWAYS a dict.

    `net_capital=None` keeps its meaning: the net-capital cap is inert (the
    basis is unlevered or stale). `existing_value=None` fails closed.
    """
    try:
        if existing_value is None:
            return _no_size(
                "holding_unknown",
                "Your current holding couldn't be read, so no add size is shown.",
                None, gross_book, net_capital)
        reason = risk.sizing_unavailable_reason(
            gross_book, entry, stop, SINGLE_NAME_CEILING,
            net_capital, NET_CAPITAL_POSITION_CAP_PCT, existing_value)
        if reason == "holding":
            return _no_size(
                "holding_unknown",
                "Your current holding couldn't be read, so no add size is shown.",
                None, gross_book, net_capital)
        if reason is not None:
            return _no_size(reason, _reason_text(reason, entry, gross_book, net_capital,
                                                 float(existing_value)),
                            float(existing_value), gross_book, net_capital)
        ps = risk.position_sizing(
            gross_book, risk_pct, entry, stop, SINGLE_NAME_CEILING,
            net_capital=net_capital, max_capital_pct=NET_CAPITAL_POSITION_CAP_PCT,
            existing_value=existing_value)
        if ps is None:
            return _no_size("error", "Sizing couldn't run — no size is shown.",
                            float(existing_value), gross_book, net_capital)
        binding = ("capital" if ps.get("capital_capped")
                   else "ceiling" if ps.get("ceiling_capped") else "risk")
        text = ""
        if ps.get("room_capped"):
            # Word the note on the cap that actually bound (risk.py applies the
            # capital cap after the ceiling, so "capital" wins when both bind).
            if (binding == "capital" and net_capital is not None
                    and ps.get("post_capital_pct") is not None):
                now = _pct(ps.get("existing_value"), net_capital)
                text = (f"Capped by what you already hold — {now:.1f}% → "
                        f"{ps['post_capital_pct']:.1f}% of net capital")
            else:
                now = _pct(ps.get("existing_value"), gross_book)
                text = (f"Capped by what you already hold — {now:.1f}% → "
                        f"{ps.get('post_portfolio_pct', 0.0):.1f}% of the book")
        return {"state": "sized", "ps": ps, "binding": binding, "text": text}
    except Exception:
        return {"state": "no_size", "code": "error",
                "text": "Sizing couldn't run — no size is shown.",
                "existing_gross_pct": None, "existing_capital_pct": None}


def room_note(sizing) -> "str | None":
    """The "capped by what you already hold" caption for a SIZED add, or None.

    Reads the FINAL (post-budget) sizing: None unless `room_capped is True` and
    a `shares` key is present. Plain text; no markdown, no dollar signs.
    """
    try:
        if not isinstance(sizing, dict) or sizing.get("room_capped") is not True:
            return None
        if "shares" not in sizing:
            return None
        ex = sizing.get("existing_value")
        if (sizing.get("room_binding") == "capital"
                and sizing.get("post_capital_pct") is not None
                and sizing.get("net_capital") is not None
                and ex is not None):
            now = _pct(ex, sizing["net_capital"])
            if now is not None:
                return (f"Capped by what you already hold — {now:.1f}% → "
                        f"{float(sizing['post_capital_pct']):.1f}% of net capital "
                        f"(cap {int(NET_CAPITAL_POSITION_CAP_PCT)}%)")
        pv = sizing.get("portfolio_value")
        now = _pct(ex, pv)
        after = sizing.get("post_portfolio_pct")
        if now is None or after is None:
            return None
        return (f"Capped by what you already hold — {now:.1f}% → "
                f"{float(after):.1f}% of the book (cap {int(SINGLE_NAME_CEILING)}%)")
    except Exception:
        return None


def _reason_text(reason, entry, gross_book, net_capital, existing) -> str:
    if reason == "stop":
        return "The stop isn't below the entry price, so no size can be worked out."
    if reason == "portfolio":
        return "Your portfolio value isn't loaded, so any share count would be a guess."
    if reason == "ceiling":
        return (f"One share alone is above the {int(SINGLE_NAME_CEILING)}% single-name "
                "limit for your book, so no size is shown.")
    if reason == "capital":
        return (f"One share alone is above the {int(NET_CAPITAL_POSITION_CAP_PCT)}% "
                "net-capital cap (or your net capital is at or below zero), so no size is shown.")
    if reason == "room_capital":
        a = _pct(existing, net_capital)
        b = _pct(existing + float(entry), net_capital)
        return (f"You already hold {a:.1f}% of your net capital here; one more share "
                f"would make it {b:.1f}% (cap {int(NET_CAPITAL_POSITION_CAP_PCT)}%).")
    if reason == "room_ceiling":
        a = _pct(existing, gross_book)
        b = _pct(existing + float(entry), gross_book)
        return (f"You already hold {a:.1f}% of your book here; one more share "
                f"would make it {b:.1f}% (cap {int(SINGLE_NAME_CEILING)}%).")
    return "Sizing couldn't run — no size is shown."


# ── held_add_verdict ──────────────────────────────────────────────────────────

_PAUSE_BUCKETS = (
    "card_blocked_adds",
    "deterioration_blocked_adds",
    "cooldown_adds",
    "sector_blocked_adds",
    "risk_blocked_adds",
    "concentration_blocked_adds",
    "pair_blocked_adds",
    "cluster_blocked_adds",
    "entry_held_adds",
    "room_blocked_adds",
)

_UNKNOWN_REASON = "Grow Today's add call for today isn't available — open 🏠 Home to build it."


def _iso(d):
    if d is None:
        return None
    s = str(d).strip()
    return s[:10] if s else None


def _verdict(state, reason, gate_id=None, item=None) -> dict:
    return {"state": state, "reason": reason, "gate_id": gate_id, "item": item}


def held_add_verdict(ticker, grow, *, brief_date, today, brief_epoch, epoch) -> dict:
    """What Grow Today decided today about adding to this held name.

    State: "add" | "paused" | "not_called" | "unknown". Read-only over the raw
    `grow_today` dict. Never raises (any failure -> "unknown").
    """
    try:
        if not isinstance(grow, dict):
            return _verdict("unknown", _UNKNOWN_REASON)
        bd, td = _iso(brief_date), _iso(today)
        if bd is None or td is None or bd != td:
            return _verdict("unknown", _UNKNOWN_REASON)
        if brief_epoch is None or brief_epoch != epoch:
            return _verdict("unknown", _UNKNOWN_REASON)
        want = str(ticker).strip().upper()

        def _find(bucket):
            items = grow.get(bucket)
            if not isinstance(items, (list, tuple)):
                return None   # absent bucket (or garbage): nothing in it for this ticker
            for it in items:
                if isinstance(it, dict) and str(it.get("ticker", "")).strip().upper() == want:
                    return it
            return None

        # Pause buckets first: if a ticker were ever in both, fail closed.
        for b in _PAUSE_BUCKETS:
            it = _find(b)
            if it is not None:
                if b == "card_blocked_adds":
                    reason = f"Paused by a card in today's Brief ({it.get('where') or 'Card'})"
                else:
                    reason = it.get("reason") or "Grow Today paused adds to this name today."
                return _verdict("paused", reason, it.get("gate_id"), it)

        it = _find("add_positions")
        if it is not None:
            return _verdict("add", "Grow Today's add call today", None, it)

        tone = grow.get("tone")
        if tone == "bear":
            return _verdict("not_called", "Market risk-off today — Grow Today makes no add calls.")
        if tone != "bull":
            ap = _find("adds_paused")
            reason = (ap.get("reason") if ap else None) or "Grow Today only suggests adds on an up day."
            return _verdict("not_called", reason)
        return _verdict(
            "not_called",
            f"Not an add candidate today — Grow Today adds need a Strong Buy, "
            f"composite ≥ {COMPOSITE_BUY:.0f} and ≥ {ADD_WINNER_MIN_GAP_PCT:.0f}% above the stop.")
    except Exception:
        return _verdict("unknown", _UNKNOWN_REASON)


# ── concentration_tile ────────────────────────────────────────────────────────

def concentration_tile(*, existing_value, add_cost, gross_book, net_capital) -> dict:
    """{"state": True|False|None, "detail": str} for the Analysis tile.

    state False = at/above a cap now or after the add; None = can't tell.
    """
    try:
        if existing_value is None or not _finite(existing_value):
            return {"state": None, "detail": "Holding unknown"}
        if not _finite(gross_book) or not float(gross_book) > 0:
            return {"state": None, "detail": "See Account page"}
        ex = float(existing_value)
        has_add = add_cost is not None and _finite(add_cost) and float(add_cost) > 0
        add = float(add_cost) if has_add else 0.0
        gb = float(gross_book)
        now_g = ex / gb * 100.0
        # Post-trade denominator, as concentration.assess_add_concentration does:
        # the add also grows the book, so an add landing exactly on the cap of
        # the pre-trade book must not read as a breach.
        after_g = (ex + add) / (gb + add) * 100.0
        net_ok = net_capital is not None and _finite(net_capital) and float(net_capital) > 0
        parts = []
        if has_add:
            parts.append(f"{now_g:.1f}% → {after_g:.1f}% of book")
        else:
            parts.append(f"{now_g:.1f}% of book")
        breach = (after_g if has_add else now_g) >= SINGLE_NAME_CEILING
        if net_ok:
            nc = float(net_capital)
            now_c = ex / nc * 100.0
            after_c = (ex + add) / nc * 100.0
            parts.append(f"{now_c:.1f}% → {after_c:.1f}% of net capital" if has_add
                         else f"{now_c:.1f}% of net capital")
            breach = breach or (after_c if has_add else now_c) >= NET_CAPITAL_POSITION_CAP_PCT
        return {"state": not breach, "detail": " · ".join(parts)}
    except Exception:
        return {"state": None, "detail": "See Account page"}
