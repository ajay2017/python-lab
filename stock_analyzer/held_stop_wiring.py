"""Gate module: builds the held-position stop for every holding (finding #26 C3).

`held_stop.py` is the RULE (pure, one ticker, explicit parameters). This module
is the DRIVER the app, the cron and the headless engine all call, so every
surface judges a breach against the same number:

  build_held_stops(holdings, held_data, trades_df, as_of, *, fetch_history)
      -> {TICKER: result dict}      (never raises)

Fallback chain per ticker (each step says so in `note`; nothing is silent):
  1. journal readable + share count agrees: a LONG split-adjusted,
     dividend-UNadjusted frame from `fetch_history(ticker, period)` (period from
     `held_stop.history_period_needed`) -> C1's own basis (full / partial / ...).
  2. that fetch returned nothing, raised, or is out of date: the app's bundle
     frame `held_data[t]["df"]` (6 months, dividend-ADJUSTED, so past closes
     drift) -> basis forced to "partial" with a note.
  3. journal unreadable / no history / count mismatch, or no usable frame:
     C1's "prior_session" (last completed close minus the wider multiple x ATR)
     or "unavailable" (no stop at all).

Policy values are read from `constants` at call time: the entry multiple is
`ATR_STOP_MULT`, the trailing one `HELD_TRAIL_ATR_MULT`, the go-live date
`HELD_TRAIL_START`, plus `HELD_STOP_*` tolerances and `STOP_RATCHET_LEVELS`.

A manual stop is NOT applied here (`held_stop.compose_with_manual` does that);
this module only builds the AUTOMATIC stop. Not wired as of the commit that added it.
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd

from stock_analyzer import constants as _C
from stock_analyzer import data as _data
from stock_analyzer import held_stop as hs
from stock_analyzer.act_today_precedence import is_stop_breached

# basis values that mean "do not claim the stop only moves up / full history"
_DEGRADED_BASES = frozenset({"partial", "prior_session", "unavailable"})

LABEL_ENTRY = "Entry Stop"
LABEL_TRAILING = "Trailing Stop"
LABEL_PRIOR = "Prior-close ATR"
LABEL_UNAVAILABLE = "Stop Unavailable"


# ── small helpers ────────────────────────────────────────────────────────────
def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ticker(h) -> str:
    return str(h.get("Ticker", h.get("ticker", "")) or "").strip().upper()


def _exact_shares(h) -> float:
    """Exact (possibly fractional) share count of a holdings row. Prefers
    "Shares Exact" (port_df), then "Shares" / "shares". The int-truncated
    "Shares" of a port_df row would trip the journal check on a fractional
    holding, so callers should pass raw holdings or a row with "Shares Exact"."""
    for key in ("Shares Exact", "Shares", "shares"):
        if key in h:
            x = _f(h.get(key))
            if x is not None:
                return x
    return 0.0


def _avg_cost(h) -> float:
    for key in ("Avg Cost ($)", "avg_cost", "Avg Cost"):
        if key in h:
            x = _f(h.get(key))
            if x is not None:
                return x
    return 0.0


def _trail_start() -> date:
    return date.fromisoformat(str(_C.HELD_TRAIL_START))


def _prev_trading_day(as_of: date) -> date:
    d = as_of
    while True:
        d = date.fromordinal(d.toordinal() - 1)
        if _data.is_trading_day(d):
            return d


def _policy() -> dict:
    return dict(
        initial_mult=float(_C.ATR_STOP_MULT),
        trail_mult=float(_C.HELD_TRAIL_ATR_MULT),
        trail_start=_trail_start(),
        ratchet_levels=_C.STOP_RATCHET_LEVELS,
        warmup_bars=int(_C.HELD_STOP_WARMUP_BARS),
        min_bars=int(_C.HELD_STOP_MIN_BARS),
        fill_tol_pct=float(_C.HELD_STOP_FILL_TOL_PCT),
    )


def _usable_frame(df) -> bool:
    return (isinstance(df, pd.DataFrame) and len(df) > 0
            and all(c in df.columns for c in ("High", "Low", "Close")))


def _bundle_frame(held_data, ticker: str):
    r = held_data.get(ticker) if isinstance(held_data, dict) else None
    df = r.get("df") if isinstance(r, dict) else None
    return df if _usable_frame(df) else None


def fetch_or_raise(ticker: str, period: str):
    """`fetch_history` for production callers: the unadjusted Yahoo frame, or
    RuntimeError when there is none. Raising (rather than returning None) lets a
    caching wrapper such as `st.cache_data` avoid caching a failure."""
    df = _data.fetch_unadjusted_history(ticker, period)
    if df is None:
        raise RuntimeError(f"unadjusted price history unavailable for {ticker}")
    return df


# ── labels and results ───────────────────────────────────────────────────────
def label_for(basis: str, binding, ratchet_label=None) -> str:
    if basis == "unavailable":
        return LABEL_UNAVAILABLE
    if basis == "prior_session":
        return LABEL_PRIOR
    if binding == "ratchet":
        return str(ratchet_label or "Profit Lock")
    if binding == "trailing":
        return LABEL_TRAILING
    return LABEL_ENTRY


def _ep_reason_text(ep: dict) -> str:
    return str((ep or {}).get("reason") or "").strip() or "the start of this position is unknown"


def _wrap(ticker: str, res: dict, note: str, avg_cost: float, *, fell_back: bool = False) -> dict:
    basis = res["basis"]
    return {
        "ticker": ticker,
        "stop": res["stop"],
        "basis": basis,
        "label": label_for(basis, res.get("binding"), res.get("ratchet_label")),
        "binding": res.get("binding"),
        "since": res.get("since"),
        "last_session": res.get("last_session"),
        "components": res.get("components"),
        "hw_close": res.get("hw_close"),
        "hw_session": res.get("hw_session"),
        "ratchet_label": res.get("ratchet_label"),
        "atr_last": res.get("atr_last"),
        "avg_cost": avg_cost,
        "note": note,
        "degraded": basis in _DEGRADED_BASES,
        "fell_back": fell_back,
        "buy_day": basis == "buy_day",
    }


def unavailable_result(ticker: str, note: str, avg_cost: float = 0.0) -> dict:
    """A degraded "Stop Unavailable" result (public: the app uses it for a ticker
    whose build raised, so it still reaches `degraded_notes`)."""
    return _wrap(ticker, hs._result(None, "unavailable", None, note), note, avg_cost)


_unavailable = unavailable_result


def _buy_day(t: str, ep: dict, bundle, avg_cost: float, as_of: date, pol: dict,
             fetch_history) -> dict:
    """Owner decision 2026-10-30 (second): a position bought TODAY has no completed
    session of its own, so its stop is the entry component the NEXT session will
    compute: the recorded fill (split-adjusted) minus ATR_STOP_MULT x the ATR at
    the last completed session (`held_stop.entry_anchor_atr`, seeded like C1's
    entry anchor). The fill is fixed, so today's price cannot move it. Not
    degraded. A missing fill or ATR falls back to the ordinary C1 prior_session
    result, which IS degraded and says why."""
    df = bundle
    if df is None:
        try:
            df = fetch_history(t, "1y")
        except Exception:
            df = None
    if not _usable_frame(df):
        return unavailable_result(t, "No price history is available for this position.", avg_cost)
    fill = _f(ep.get("first_buy_price"))
    ratio = _f(ep.get("split_ratio")) or 1.0
    atr = hs.entry_anchor_atr(hs.completed_bars(df, as_of), ep.get("start"), pol["warmup_bars"])
    if fill is not None and fill > 0 and atr is not None and ratio > 0:
        stop = round(fill / ratio - float(pol["initial_mult"]) * atr, 2)
        if stop > 0:
            note = (f"Bought today: the stop is your fill price \\${fill / ratio:.2f} minus "
                    f"{_C.ATR_STOP_MULT:g} × ATR (from the last completed session). From the "
                    "next session the trailing and profit-lock parts can raise it.")
            res = hs._result(stop, "buy_day", "entry", "bought today", since=ep.get("start"),
                             atr_last=atr)
            return _wrap(t, res, note, avg_cost)
    ep_fb = {"status": "no_history",
             "reason": "no usable fill price or ATR for today's buy"}
    res = hs.compute_held_stop(df, ep_fb, avg_cost, as_of, **pol)
    return _wrap(t, res, _note_for(res, ep_fb, bundle=False, n_sessions=None,
                                   fetch_problem=None), avg_cost)


def _note_for(res: dict, ep: dict, *, bundle: bool, n_sessions: int | None,
              fetch_problem: str | None) -> str:
    """Plain-English note; empty only for a clean full basis. Never claims the
    stop 'only moves up' for a partial / prior_session / unavailable basis."""
    basis = res["basis"]
    reason = str(res.get("reason") or "").strip()
    if basis == "unavailable":
        return (f"No stop could be built: {reason}." if reason
                else "No stop could be built: price history is missing.")
    if basis == "prior_session":
        mult = max(float(_C.ATR_STOP_MULT), float(_C.HELD_TRAIL_ATR_MULT))
        why = (_ep_reason_text(ep) if (ep or {}).get("status") != "ok" else reason)
        return (f"This position's start isn't usable ({why}), so the stop is the last "
                f"completed close minus {mult:g} x ATR. It does not trail, and it can "
                "move down as the price falls.")
    parts: list[str] = []
    if bundle:
        n = f" ({n_sessions} sessions)" if n_sessions else ""
        parts.append(
            (fetch_problem or "Longer price history couldn't be loaded") + ", so this stop uses "
            f"the app's shorter 6-month price history{n}; it is a lower bound and can change "
            "when the history changes.")
    if basis == "partial":
        if reason:
            parts.append(reason[0].upper() + reason[1:] + ".")
        if not bundle and not reason:
            parts.append("Price history doesn't cover this whole position, so this stop is a lower bound.")
    elif reason and basis == "full":
        parts.append(reason[0].upper() + reason[1:] + ".")
    return " ".join(parts)


# ── one ticker ───────────────────────────────────────────────────────────────
def _long_frame(ticker: str, ep: dict, as_of: date, pol: dict, fetch_history):
    """(frame or None, problem text). Sized by history_period_needed; re-checked
    once against the fetched frame, as the dry run does."""
    def _get(period):
        df = fetch_history(ticker, period)
        return df if _usable_frame(df) else None

    period = hs.history_period_needed(None, ep.get("start"), pol["trail_start"],
                                      pol["warmup_bars"], as_of=as_of) or "1y"
    try:
        df = _get(period)
    except Exception:
        return None, "Longer price history couldn't be loaded"
    if df is None:
        return None, "Longer price history came back empty"
    try:
        first = hs._et_dates(df.index)[0].date()
        again = hs.history_period_needed(first, ep.get("start"), pol["trail_start"],
                                         pol["warmup_bars"], as_of=as_of)
        if again is not None and again != period and period != "max":
            try:
                bigger = _get(again)
            except Exception:
                bigger = None
            if bigger is not None:
                df = bigger
    except Exception:
        pass
    bars = hs.completed_bars(df, as_of)
    if len(bars) == 0 or bars.index[-1].date() < _prev_trading_day(as_of):
        last = bars.index[-1].date() if len(bars) else "no completed session"
        return df, f"Longer price history is out of date (it ends {last})"
    return df, None


def _one(h: dict, held_data, trades_df, as_of: date, fetch_history, pol: dict) -> dict:
    t = _ticker(h)
    shares, avg_cost = _exact_shares(h), _avg_cost(h)
    ep = hs.episode_from_journal(t, trades_df, shares, shares_tol=float(_C.HELD_STOP_SHARES_TOL))
    kw = {k: v for k, v in pol.items()}
    bundle = _bundle_frame(held_data, t)

    def run(df, **extra):
        return hs.compute_held_stop(df, ep, avg_cost, as_of, **kw)

    if ep.get("status") == "ok" and ep.get("start") is not None and ep["start"] >= as_of:
        return _buy_day(t, ep, bundle, avg_cost, as_of, pol, fetch_history)

    if ep.get("status") != "ok":
        df = bundle
        if df is None:
            try:
                df = fetch_history(t, "1y")
            except Exception:
                df = None
        if not _usable_frame(df):
            return _unavailable(t, "No price history is available for this position.", avg_cost)
        res = run(df)
        return _wrap(t, res, _note_for(res, ep, bundle=False, n_sessions=None, fetch_problem=None),
                     avg_cost)

    long_df, problem = _long_frame(t, ep, as_of, pol, fetch_history)
    if long_df is not None and problem is None:
        res = run(long_df)
        return _wrap(t, res, _note_for(res, ep, bundle=False, n_sessions=None, fetch_problem=None),
                     avg_cost)

    # fallback: the app's bundle frame, always "partial"
    problem = problem or "Longer price history couldn't be loaded"
    frame = bundle if bundle is not None else long_df
    if frame is None:
        return _unavailable(t, problem + " and the app's own price history is missing.", avg_cost)
    res = run(frame)
    if res["basis"] == "unavailable" and bundle is not None and long_df is not None:
        res = run(long_df)     # a stale long frame beats a too-short bundle
        frame = long_df
    n = len(hs.completed_bars(frame, as_of))
    if res["basis"] == "full":
        res = {**res, "basis": "partial"}
    note = _note_for(res, ep, bundle=True, n_sessions=n, fetch_problem=problem)
    if res["basis"] in ("prior_session", "unavailable"):
        note = _note_for(res, ep, bundle=False, n_sessions=None, fetch_problem=None)
    return _wrap(t, res, note, avg_cost, fell_back=True)


def build_held_stops(holdings, held_data, trades_df, as_of: date, *, fetch_history) -> dict:
    """{TICKER: held-stop result} for every valid holding. NEVER raises: a ticker
    whose build throws becomes a degraded "Stop Unavailable" result with a note.

    holdings: rows with a ticker, exact shares ("Shares Exact" / "Shares" /
    "shares") and average cost. held_data: {ticker: load_all result} (its "df" is
    the 6-month bundle frame). trades_df: the strict journal read (None =
    unreadable). as_of: ET `date` (always passed in). fetch_history(ticker,
    period) -> unadjusted OHLCV frame; may raise or return None."""
    out: dict[str, dict] = {}
    try:
        pol = _policy()
        rows = list(holdings.to_dict("records")) if isinstance(holdings, pd.DataFrame) else list(holdings or [])
    except Exception as exc:
        return {_ticker(h): _unavailable(_ticker(h), f"Stop could not be computed ({type(exc).__name__}).")
                for h in (holdings if isinstance(holdings, list) else []) if isinstance(h, dict) and _ticker(h)}
    for h in rows:
        try:
            t = _ticker(h)
            if not t or _exact_shares(h) <= 0 or _avg_cost(h) <= 0:
                continue
            out[t] = _one(h, held_data, trades_df, as_of, fetch_history, pol)
        except Exception as exc:
            try:
                t = _ticker(h)
            except Exception:
                continue
            if t:
                out[t] = _unavailable(
                    t, f"Stop could not be computed ({type(exc).__name__}).", 0.0)
    return out


# ── consumers ────────────────────────────────────────────────────────────────
def degraded(h) -> bool:
    """True when this ticker's stop is not on a full basis (partial /
    prior_session / unavailable). A missing result counts as degraded."""
    return not isinstance(h, dict) or h.get("basis") in _DEGRADED_BASES or h.get("basis") is None


def degraded_notes(held_stops) -> list[tuple[str, str, str]]:
    """[(ticker, label, note)] for every degraded result, sorted by ticker: the
    rows of the degraded-basis banner."""
    if not isinstance(held_stops, dict):
        return []
    return [(t, str(h.get("label") or ""), str(h.get("note") or ""))
            for t, h in sorted(held_stops.items()) if degraded(h)]


def analysis_manual_override(auto_stop, manual) -> tuple[float | None, bool]:
    """The Analysis page's manual-stop merge: (stop, is_manual). `manual` is a
    manual_stops row ({"stop_price": ...}), a bare price, or None. The same rule
    as build_portfolio_df: a manual stop wins only when >= the auto stop, and
    applies on its own when there is NO auto stop."""
    mp = manual.get("stop_price") if isinstance(manual, dict) else manual
    return hs.compose_with_manual(_f(auto_stop), _f(mp))


def brief_breached_set(port_df) -> set[str]:
    """Tickers whose Price is at or past their Stop by the app's own breach rule
    (`act_today_precedence.is_stop_breached`, gap rounded to
    GAP_TO_STOP_ROUND_DECIMALS). A NaN stored gap is a data issue, never a breach."""
    if port_df is None or not isinstance(port_df, pd.DataFrame) or port_df.empty:
        return set()
    if not {"Ticker", "Price", "Stop"} <= set(port_df.columns):
        return set()
    has_gap = "Gap to Stop (%)" in port_df.columns
    out: set[str] = set()
    for _, r in port_df.iterrows():
        price, stop = _f(r.get("Price")), _f(r.get("Stop"))
        gap_missing = has_gap and _f(r.get("Gap to Stop (%)")) is None
        if is_stop_breached(price, stop, gap_missing=gap_missing):
            out.add(str(r.get("Ticker") or "").strip().upper())
    out.discard("")
    return out


def held_sig(port_df) -> frozenset:
    """Hashable signature of every holding's stop (ticker, stop to the cent,
    basis) for the Home memo: a changed stop or basis rebuilds the Brief."""
    if port_df is None or not isinstance(port_df, pd.DataFrame) or port_df.empty \
            or "Ticker" not in port_df.columns:
        return frozenset()
    has_stop = "Stop" in port_df.columns
    has_basis = "Stop Basis" in port_df.columns
    sig = []
    for _, r in port_df.iterrows():
        stop = _f(r.get("Stop")) if has_stop else None
        basis = r.get("Stop Basis") if has_basis else None
        sig.append((str(r.get("Ticker") or "").strip().upper(),
                    None if stop is None else round(stop, 2),
                    None if basis is None or (isinstance(basis, float) and basis != basis) else str(basis)))
    return frozenset(sig)
