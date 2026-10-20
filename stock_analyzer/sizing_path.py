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

def _norm_set(tickers) -> set:
    if not isinstance(tickers, (list, tuple, set, frozenset)):
        return set()
    return {str(t).strip().upper() for t in tickers if str(t).strip()}


def unpriced_holdings(held_tickers, port_df) -> set:
    """Tickers the app KNOWS you hold (`held_tickers`, e.g. Home's
    `_last_held_tickers`) that are missing from the enriched book -- Home drops
    a holding it couldn't price. Never read as "not held"."""
    try:
        in_book = set()
        if port_df is not None and getattr(port_df, "columns", None) is not None \
                and "Ticker" in port_df.columns:
            in_book = {str(t).strip().upper() for t in port_df["Ticker"].values}
        return _norm_set(held_tickers) - in_book
    except Exception:
        return set()


def held_value(ticker, port_df, holdings_state, held_tickers=None) -> "float | None":
    """Dollar value already held in `ticker`: 0.0 = not held, None = couldn't check.

    `held_tickers` (optional): the tickers the app knows are held. A ticker in
    it but absent from the book (a holding Home couldn't price) is None, never
    0.0 -- fail closed.

    holdings_state: "ready" (holdings loaded), "flat" (definitely holds
    nothing); anything else is unknown. Never raises.
    """
    try:
        known = holdings_state in ("ready", "flat")
        cols = getattr(port_df, "columns", None)
        usable = (port_df is not None and cols is not None and len(port_df) > 0
                  and "Ticker" in cols and "Market Value" in cols)
        if not usable:
            if str(ticker).strip().upper() in _norm_set(held_tickers):
                return None
            return 0.0 if holdings_state == "flat" else None
        want = str(ticker).strip().upper()
        if want in unpriced_holdings(held_tickers, port_df):
            return None
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


# G-01: `risk_blocked_adds[].reason` is the risk-rec TYPE, not a sentence.
RISK_ADD_REASON_LABEL = {
    "beta":   "high portfolio beta",
    "sharpe": "poor risk-adjusted return",
}


def _risk_add_reason(it) -> str:
    code = it.get("reason")
    label = RISK_ADD_REASON_LABEL.get(code) if isinstance(code, str) else None
    if label:
        return f"Risk Advisor flagged this holding for trim due to {label}."
    title = it.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    if isinstance(code, str) and code.strip():
        return code.strip()
    return "Risk Advisor flagged this holding for trim, so Grow Today paused adds."


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
                elif b == "risk_blocked_adds":
                    reason = _risk_add_reason(it)
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
        # DISPLAY the pre-trade denominator (it equals room_note / post_portfolio_pct
        # on the Grow Today card); the ✗ test below uses the post-trade one, as
        # concentration.assess_add_concentration does, so an add that lands
        # exactly on the cap of the pre-trade book never reads as a breach.
        after_g = (ex + add) / gb * 100.0
        after_g_post = (ex + add) / (gb + add) * 100.0
        net_ok = net_capital is not None and _finite(net_capital) and float(net_capital) > 0
        fresh = has_add and ex == 0
        parts = []
        if fresh:
            parts.append(f"{after_g:.1f}% of book")
        elif has_add:
            parts.append(f"{now_g:.1f}% → {after_g:.1f}% of book")
        else:
            parts.append(f"{now_g:.1f}% of book")
        breach = (after_g_post if has_add else now_g) >= SINGLE_NAME_CEILING
        if net_ok:
            nc = float(net_capital)
            now_c = ex / nc * 100.0
            after_c = (ex + add) / nc * 100.0
            if fresh:
                parts.append(f"{after_c:.1f}% of net capital")
            elif has_add:
                parts.append(f"{now_c:.1f}% → {after_c:.1f}% of net capital")
            else:
                parts.append(f"{now_c:.1f}% of net capital")
            breach = breach or (after_c if has_add else now_c) >= NET_CAPITAL_POSITION_CAP_PCT
        detail = " · ".join(parts)
        if fresh:
            detail += " after the buy"
        return {"state": not breach, "detail": detail}
    except Exception:
        return {"state": None, "detail": "See Account page"}


# ── Analysis wiring (S-D C2) ──────────────────────────────────────────────────

def holdings_state(port_state, last_held_tickers, port_df=None) -> str:
    """"ready" | "flat" | "unknown" for the Analysis page.

    "ready": the enriched book is published and non-empty. "flat": definitely
    holds nothing (Home published an EMPTY held-tickers list). Anything else is
    "unknown" -- never read as flat.
    """
    try:
        has_rows = port_df is not None and len(port_df) > 0
        if port_state == "ready" and has_rows:
            return "ready"
        if (isinstance(last_held_tickers, (list, tuple, set))
                and len(last_held_tickers) == 0):
            return "flat"
        return "unknown"
    except Exception:
        return "unknown"


_HOLDINGS_UNKNOWN_REASON = "your holdings couldn't be checked this session"


def analysis_holdings_unknown(decision, ticker=None, unpriced=False) -> dict:
    """D4: a COPY of an Analysis entry decision with no size offered because
    the holdings could not be read (a held name would be sized as a new one).
    `unpriced=True`: this ONE holding is known but Home couldn't price it."""
    out = dict(decision) if isinstance(decision, dict) else {}
    out["offer_size"] = False
    out["entry_checked"] = False
    out["holdings_unknown"] = True
    if unpriced and ticker:
        note = (f"Your {ticker} holding couldn't be priced on 🏠 Home, "
                "so no share count is shown.")
        out["reason_if_no_size"] = note.rstrip(".")
        out["holdings_note"] = note
    else:
        out["reason_if_no_size"] = _HOLDINGS_UNKNOWN_REASON
    return out


def analysis_offer_held_add(decision, verdict) -> dict:
    """A COPY of an Analysis entry decision for a HELD name, aligned with Grow
    Today's add call: the size is offered only when the verdict is "add".
    Analysis never decides an add itself. Never raises (a crash -> no size)."""
    try:
        out = dict(decision) if isinstance(decision, dict) else {}
        out["held_add"] = verdict
        if out.get("offer_size") is not True:
            out["offer_size"] = False
            return out                      # already no size: just attach the verdict
        if not isinstance(verdict, dict) or verdict.get("state") != "add":
            out["offer_size"] = False
            reason = verdict.get("reason") if isinstance(verdict, dict) else None
            out["reason_if_no_size"] = reason or "Grow Today's add call isn't available"
        else:
            item = verdict.get("item")
            sz = item.get("sizing") if isinstance(item, dict) else None
            if not (isinstance(sz, dict) and "shares" in sz):
                # Grow Today called the add but carries no share count (holding
                # unreadable / budget-withheld): never size it here instead.
                out["offer_size"] = False
                out["reason_if_no_size"] = (
                    "Grow Today couldn't read your holding, so it has no add size today"
                    if isinstance(sz, dict) and sz.get("holding_unknown")
                    else "Grow Today has no add size for this name today")
        return out
    except Exception:
        return {"offer_size": False, "held_add": verdict, "holds": [], "unchecked": [],
                "passed": [], "hold": None, "entry_checked": False, "crashed": False,
                "reason_if_no_size": "Grow Today's add call isn't available"}


def _num_or_none(x):
    return float(x) if _finite(x) else None


def add_call_box(verdict, *, ps=None, size_text=None, same_size=False, built_at=None) -> dict:
    """The "➕ Add ..." box Analysis shows on a HELD Buy: Grow Today's call,
    never Analysis's own. `{tone, title, body, source, show_home}` with tone in
    "green" | "amber" | "grey". A missing built_at / score drops that fragment
    (never prints "None"). `ps` is the FINAL (post-budget) sizing or None."""
    v = verdict if isinstance(verdict, dict) else {}
    state = v.get("state")
    _it = v.get("item")
    item = _it if isinstance(_it, dict) else {}
    score = _num_or_none(item.get("score"))
    if score is None:
        score = _num_or_none(item.get("composite_score"))
    frag = [f"Grow Today's call, built {built_at} ET" if built_at else "Grow Today's call"]
    if score is not None:
        frag.append(f"composite there {score:.0f}")
    reason = str(v.get("reason") or "").strip()
    if state == "add":
        sh = ps.get("shares") if isinstance(ps, dict) else None
        if _finite(sh) and float(sh) >= 1:
            src = " · ".join(frag) + "."
            if same_size:
                src += " Same size as Grow Today's card."
            return {"tone": "green", "show_home": False, "source": src,
                    "title": f"➕ Add — Grow Today's call today: {int(float(sh))} shares",
                    "body": room_note(ps) or ""}
        return {"tone": "green", "show_home": False,
                "source": " · ".join(frag) + ".",
                "title": "➕ Add — Grow Today's call today",
                "body": str(size_text or "No share count is shown for this name today.")}
    if state == "paused":
        if v.get("gate_id"):
            frag.append(f"gate {v['gate_id']}")
        return {"tone": "amber", "show_home": False, "source": " · ".join(frag) + ".",
                "title": "➕ Add — paused in Grow Today today",
                "body": reason or "Grow Today paused adds to this name today."}
    if state == "not_called":
        return {"tone": "grey", "show_home": False, "source": None,
                "title": "➕ No add call today", "body": reason}
    return {"tone": "grey", "show_home": True, "source": None,
            "title": "➕ Add call not available",
            "body": reason or _UNKNOWN_REASON}
