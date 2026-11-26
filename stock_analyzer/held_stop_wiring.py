"""Gate module: builds the held-position stop for every holding (finding #26 C3).

`held_stop.py` is the RULE (pure, one ticker, explicit parameters). This module
is the DRIVER the app, the cron and the headless engine all call, so every
surface judges a breach against the same number:

  build_held_stops(holdings, held_data, trades_df, as_of, *, fetch_history,
                   floors, carry, fetch_range=None)
      -> {TICKER: result dict}      (never raises)

`floors` (REQUIRED, no default; #26 C3c) is `db.load_held_stop_floors_or_none`'s
result: {TICKER: {episode_start: row}}, `{}` = read with nothing saved, `None` =
couldn't be read. `held_stop_floor.apply_floor` is applied ONCE per ticker, to the
result of the frame that produced it, so every surface reads the same effective
stop (it never changes the basis; a buy-day / unavailable result is not floored).

`carry` (REQUIRED, no default; SlidingStop C3) is `db.load_held_stop_carry_or_none`'s
result: {TICKER: saved row}, `{}` = read with nothing saved, `None` (or any non-dict) =
couldn't be read. `held_stop_carry.apply_carry` runs AFTER the floor, once per ticker,
on the same frame, and only on a partial / prior_session result: the saved carried
stop is a lower bound, so the stop never falls while the carry is trusted (it never
lowers the computed stop, and a buy-day / unavailable / rejected-fill result is never
carried: `_one` tags a buy-day return `buy_day_path` and gives it no `carry` key).
When the carry binds the label is `LABEL_CARRIED`. Every result carries
`episode_status` (the journal episode's status, None when none applies).

`fetch_range(ticker, as_of) -> {"low", "high"} | None` is consulted only for a
position bought today and only when no history frame has a row for today: Yahoo
can publish today's High / Low without a Close and the history fetches strip such
bars. None or a failure behaves exactly as if it were not passed.

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
this module only builds the AUTOMATIC stop.
"""
from __future__ import annotations

import math
from datetime import date

import pandas as pd

from stock_analyzer import constants as _C
from stock_analyzer import data as _data
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_carry as hsc
from stock_analyzer import held_stop_floor as hsf
from stock_analyzer.act_today_precedence import is_stop_breached

# The `floor.reason` prefix `_one` writes when the floor check itself raised (a code
# defect, never a data state). The EOD lane turns it red (cron_runner, C3cFloorBug).
FLOOR_CHECK_FAILED = "floor check failed"

# basis values that mean "do not claim the stop only moves up / full history"
_DEGRADED_BASES = frozenset({"partial", "prior_session", "unavailable"})

LABEL_ENTRY = "Entry Stop"
LABEL_TRAILING = "Trailing Stop"
LABEL_PRIOR = "Prior-close ATR"
LABEL_FALLBACK = "Fallback Stop"
LABEL_UNAVAILABLE = "Stop Unavailable"
LABEL_CARRIED = "Carried Stop"

# The prior-close note's trailing sentence, and what replaces it once the carry
# holds the stop (APPLIED or NOT_BINDING): "can move down" is then false while the
# carry is trusted. A test pins both strings.
NOTE_NO_TRAIL = "It does not trail, and it can move down as the price falls."
NOTE_NO_TRAIL_CARRIED = ("It does not trail on its own; a saved stop keeps it from moving "
                         "down while your trades in this position don't change.")


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
        # a buy-day stop whose fill leg is the lower one reads as the entry stop
        if binding == "range":
            return LABEL_FALLBACK
        return LABEL_ENTRY if binding == "entry" else LABEL_PRIOR
    if binding == "ratchet":
        return str(ratchet_label or "Profit Lock")
    if binding == "trailing":
        return LABEL_TRAILING
    return LABEL_ENTRY


def _ep_reason_text(ep: dict) -> str:
    return str((ep or {}).get("reason") or "").strip() or "the start of this position is unknown"


def _episode_view(ep) -> dict | None:
    """The slice of the journal episode `held_stop_floor` fingerprints (C3c-1);
    None when there is no usable episode."""
    if not isinstance(ep, dict) or ep.get("status") != "ok" or ep.get("start") is None:
        return None
    return {"start": ep.get("start"), "first_buy_price": ep.get("first_buy_price"),
            "split_ratio": ep.get("split_ratio") or 1.0, "last_buy": ep.get("last_buy")}


def _wrap(ticker: str, res: dict, note: str, avg_cost: float, *, fell_back: bool = False,
          ep: dict | None = None) -> dict:
    basis = res["basis"]
    return {
        "ticker": ticker,
        "stop": res["stop"],
        "basis": basis,
        # a floored `res` (held_stop_floor.apply_floor) carries its own label /
        # floor / stop_set_on: pass them through (the EOD writer fails closed on a
        # result with no `floor` dict, so a dropped key would silently write nothing)
        "label": res.get("label") or label_for(basis, res.get("binding"), res.get("ratchet_label")),
        "floor": res.get("floor"),
        "stop_set_on": res.get("stop_set_on"),
        "binding": res.get("binding"),
        "since": res.get("since"),
        "last_session": res.get("last_session"),
        "components": res.get("components"),
        "hw_close": res.get("hw_close"),
        "hw_session": res.get("hw_session"),
        "ratchet_label": res.get("ratchet_label"),
        "atr_last": res.get("atr_last"),
        # additive (C3c-1): what the saved-floor writer / reader need
        "episode": _episode_view(ep),
        "last_close": res.get("last_close"),
        "bind_session": res.get("bind_session"),
        "bind_close": res.get("bind_close"),
        # additive: completed bars seen / needed, set only on a too-few-bars
        # "unavailable" result (None otherwise)
        "bars": res.get("bars"),
        "need": res.get("need"),
        "avg_cost": avg_cost,
        "note": note,
        "degraded": basis in _DEGRADED_BASES,
        "fell_back": fell_back,
        "buy_day": basis == "buy_day",
        # additive (SlidingStop C3): the journal episode's status (ok / mismatch /
        # no_history / unreadable); `_one` sets it on every result it returns. The
        # Home notice tier (`held_stop_carry.notice_tier`) reads it.
        "episode_status": None,
        # additive (SlidingStop C4): the journal episode's own reason text (None when
        # absent); `_one` sets it on every result it returns. `stop_basis_notice` words
        # the Home / premarket notice from it.
        "episode_reason": None,
    }


def unavailable_result(ticker: str, note: str, avg_cost: float = 0.0) -> dict:
    """A degraded "Stop Unavailable" result (public: the app uses it for a ticker
    whose build raised, so it still reaches `degraded_notes`)."""
    return _wrap(ticker, hs._result(None, "unavailable", None, note), note, avg_cost)


_unavailable = unavailable_result


def _buy_day(t: str, ep: dict, bundle, avg_cost: float, as_of: date, pol: dict,
             fetch_history, fetch_range=None) -> dict:
    """Owner decision 2026-10-30 (second): a position bought TODAY has no completed
    session of its own, so its stop is the entry component the NEXT session will
    compute: the recorded fill (split-adjusted) minus ATR_STOP_MULT x the ATR at
    the last completed session (`held_stop.entry_anchor_atr`, seeded like C1's
    entry anchor). The fill is fixed, so today's price cannot move it.

    Frame: the fresh unadjusted long frame (same one the next session uses), else
    the app's bundle frame, else a stale long frame as a last resort. A buy day
    served only by the stale frame is DEGRADED (basis "partial", and the note
    names the date the history ends).

    Fill check (`held_stop.fill_in_band`, the same rule as the entry session's):
    the fill must sit in the band of today's range (union over every frame that
    has a today row) and the prior close, widened by `fill_tol_pct`. When NO frame
    has a today row, `fetch_range(ticker, as_of) -> {"low", "high"} | None` (if given)
    is asked for today's range alone: Yahoo can publish today's High / Low without a
    Close, and the history fetches strip such bars. Its result is re-validated
    (finite, > 0, low <= high); None, a bad value or an exception = no today row,
    exactly as before. Pass -> an
    accepted buy_day stop, not degraded. Fail (or no today bar anywhere and the
    fill is off the prior close) -> a DEGRADED prior_session result that is the
    LOWEST of up to three legs, so a genuine fill can never produce a stop above
    the price: the prior-close stop, the fill leg (fill - ATR_STOP_MULT x ATR) and,
    when a today bar exists, the range leg (bottom of the band, tolerance
    included, minus ATR_STOP_MULT x ATR; label "Fallback Stop"). With no today bar
    there is no range leg: a genuine fill is then covered by the fill leg alone,
    and a fill keyed far too high stays an acknowledged residual. A missing fill
    or ATR falls back to the plain C1 prior_session result and says why."""
    frames = []
    stale_only = False
    long_df, long_problem = _long_frame(t, ep, as_of, pol, fetch_history)
    if long_df is not None and long_problem is None:
        frames.append(long_df)
    if bundle is not None:
        frames.append(bundle)
    if long_df is not None and long_problem is not None and _usable_frame(long_df):
        frames.append(long_df)                      # stale long frame: last resort
        stale_only = len(frames) == 1
    if not frames:
        return unavailable_result(t, "No price history is available for this position.", avg_cost)
    df = frames[0]
    fill = _f(ep.get("first_buy_price"))
    ratio = _f(ep.get("split_ratio")) or 1.0
    bars = hs.completed_bars(df, as_of)
    atr = hs.entry_anchor_atr(bars, ep.get("start"), pol["warmup_bars"])
    stale_txt = ""
    if stale_only:
        _last = bars.index[-1].date() if len(bars) else "no completed session"
        stale_txt = f"The price history ends on {_last}, so this stop is built from old data. "
    reason = "no usable fill price or ATR for today's buy"
    fill_leg = range_leg = None
    has_today = rejected = False
    mult_i = float(pol["initial_mult"])
    if fill is not None and fill > 0 and atr is not None and ratio > 0:
        fill_adj = fill / ratio
        lows, highs = [], []
        for f in frames:    # union of today's ranges across frames can only widen the band
            try:
                today = f[hs._et_dates(f.index) == pd.Timestamp(as_of)]
                lo, hi = today["Low"].min(), today["High"].max()
            except Exception:
                continue
            if pd.notna(lo) and pd.notna(hi):
                lows.append(float(lo))
                highs.append(float(hi))
        if not lows and fetch_range is not None:
            # no frame has a today row (Yahoo can publish today's High / Low without a
            # Close, and the history fetches strip such bars): ask for the range alone
            try:
                _rng = fetch_range(t, as_of)
            except Exception:
                _rng = None
            if isinstance(_rng, dict):
                _rlo, _rhi = _f(_rng.get("low")), _f(_rng.get("high"))
                if _rlo is not None and _rhi is not None and 0 < _rlo <= _rhi:
                    lows.append(_rlo)
                    highs.append(_rhi)
        has_today = bool(lows)
        prev_close = float(bars["Close"].iloc[-1]) if len(bars) else None
        stop = round(fill_adj - mult_i * atr, 2)
        if hs.fill_in_band(fill_adj, min(lows) if lows else None,
                           max(highs) if highs else None, prev_close, pol["fill_tol_pct"]):
            if stop > 0:
                if stale_only:
                    note = (f"Bought today. {stale_txt}The stop is your fill price "
                            f"\\${fill_adj:.2f} minus {_C.ATR_STOP_MULT:g} × ATR (from the "
                            "last completed session in that history).")
                    res = hs._result(stop, "partial", "entry", "bought today; history is stale",
                                     since=ep.get("start"), atr_last=atr,
                                     components={"entry": stop, "trailing": None, "ratchet": None})
                    return _wrap(t, res, note, avg_cost, fell_back=True)
                note = (f"Bought today: the stop is your fill price \\${fill_adj:.2f} minus "
                        f"{_C.ATR_STOP_MULT:g} × ATR (from the last completed session). From the "
                        "next session it is rebuilt from completed sessions: the trailing and "
                        "profit-lock parts can raise it, or it can come out lower if a later session "
                        "has to use a fallback basis.")
                res = hs._result(stop, "buy_day", "entry", "bought today", since=ep.get("start"),
                                 atr_last=atr)
                return _wrap(t, res, note, avg_cost)
        else:
            rejected = True
            if stop > 0:
                fill_leg = stop
            if has_today:
                band_lo = min(lows)
                if prev_close is not None:
                    band_lo = min(band_lo, prev_close)
                _rl = round(band_lo * (1 - float(pol["fill_tol_pct"]) / 100.0) - mult_i * atr, 2)
                if _rl > 0:
                    range_leg = _rl
            reason = ("today's recorded fill price is outside today's trading range and the "
                      "prior close (a mis-keyed fill or an unrecorded split)")
    ep_fb = {"status": "no_history", "reason": reason}
    res = hs.compute_held_stop(df, ep_fb, avg_cost, as_of, **pol)
    if rejected and res.get("stop") is not None:
        prior = round(float(res["stop"]), 2)
        legs = [(prior, 0, "prior_session")]
        if fill_leg is not None:
            legs.append((fill_leg, 1, "entry"))
        if range_leg is not None:
            legs.append((range_leg, 2, "range"))
        low_v, _, binding = min(legs)
        mult = max(float(_C.ATR_STOP_MULT), float(_C.HELD_TRAIL_ATR_MULT))
        lead = ("Bought today, but your recorded fill price is outside today's trading range "
                "and the prior close (a mis-keyed fill or an unrecorded split)" if has_today else
                "Bought today, but your recorded fill price couldn't be checked against "
                "today's trading range yet")
        parts = [f"the last completed close minus {mult:g} × ATR (\\${prior:.2f})"]
        if fill_leg is not None:
            parts.append(f"your fill price \\${fill / ratio:.2f} minus "
                         f"{_C.ATR_STOP_MULT:g} × ATR (\\${fill_leg:.2f})")
        if range_leg is not None:
            parts.append(f"the lower of today's low and the prior close (less the "
                         f"{float(pol['fill_tol_pct']):g}% tolerance) minus {_C.ATR_STOP_MULT:g} × ATR (\\${range_leg:.2f})")
        if len(parts) == 1:
            pick = f"The stop is {parts[0]}"
        else:
            head = "lower" if len(parts) == 2 else "lowest"
            pick = f"The stop is the {head} of " + ", ".join(parts[:-1]) + f" and {parts[-1]}"
        applies = {"prior_session": "prior-close", "entry": "fill-based",
                   "range": "range-based"}[binding]
        note = (f"{lead}. {stale_txt}{pick}; the {applies} level applies. It is a "
                "fallback level because the fill couldn't be confirmed. It does not trail and "
                "is rebuilt each session.")
        comps = {"entry": fill_leg if binding == "entry" else None,
                 "trailing": None, "ratchet": None}
        res = {**res, "stop": low_v, "binding": binding, "components": comps}
        return _wrap(t, res, note, avg_cost)
    fb_note = _note_for(res, ep_fb, bundle=False, n_sessions=None, fetch_problem=None)
    return _wrap(t, res, (stale_txt + fb_note).strip() if stale_txt else fb_note, avg_cost)


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
        if (ep or {}).get("status") == "ok":
            # a young listing: the start IS usable, but no part of the full stop can be
            # computed yet (the old "start isn't usable" wording was false here)
            why = reason or "too little price history before the entry"
            return (f"No part of the full stop can be computed yet ({why}), so the stop "
                    f"is the last completed close minus {mult:g} x ATR. {NOTE_NO_TRAIL}")
        return (f"This position's start isn't usable ({_ep_reason_text(ep)}), so the stop is "
                f"the last completed close minus {mult:g} x ATR. {NOTE_NO_TRAIL}")
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


def _one(h: dict, held_data, trades_df, as_of: date, fetch_history, pol: dict,
         floors, carry, fetch_range=None) -> dict:
    t = _ticker(h)
    shares, avg_cost = _exact_shares(h), _avg_cost(h)
    ep = hs.episode_from_journal(t, trades_df, shares, shares_tol=float(_C.HELD_STOP_SHARES_TOL))
    kw = {k: v for k, v in pol.items()}
    bundle = _bundle_frame(held_data, t)

    def run(df, **extra):
        return hs.compute_held_stop(df, ep, avg_cost, as_of, **kw)

    def floored(out: dict, frame) -> dict:
        """The saved floor, applied once to `out` with the frame that produced it.
        `floors is None` (or not a dict) = the saved floors couldn't be read."""
        readable = isinstance(floors, dict)
        rows = floors.get(t, {}) if readable else None
        try:
            return hsf.apply_floor(out, rows, ep=ep, avg_cost=avg_cost, frame=frame,
                                   as_of=as_of, readable=readable)
        except Exception as e:      # a floor bug must never remove today's stop
            kept = dict(out)
            kept["floor"] = {"state": hsf.UNREADABLE, "value": None, "set_on": None,
                             "reason": f"{FLOOR_CHECK_FAILED}: {type(e).__name__}",
                             "note": "", "row_as_of": None, "computed": out.get("stop")}
            kept["stop_set_on"] = None
            return kept

    def carried(out: dict, frame) -> dict:
        """The saved carried stop (SlidingStop C3), applied once to the FLOORED `out`
        with the frame that produced it. `carry` not a dict = the saved rows couldn't
        be read. `apply_carry` never raises and never lowers; a fault in the inputs
        computed here becomes the same FAILED shape (the stop stays, the EOD goes red)."""
        readable = isinstance(carry, dict)
        row = carry.get(t) if readable else None
        # additive (SlidingStop C4): what the saved row said, copied for the U3 note
        # (a full stop below the carried one) in `stop_basis_notice.drop_rows`.
        # Read-only: it never feeds a stop. Absent when no row exists for this name.
        seen = None
        if isinstance(row, dict):
            seen = {"stop": _f(row.get("stop")), "source_basis": row.get("source_basis"),
                    "as_of": row.get("as_of"), "stop_set_on": row.get("stop_set_on")}

        def with_seen(r: dict) -> dict:
            if seen is not None:
                r["carry_row_seen"] = dict(seen)
            return r

        try:
            res = hsc.apply_carry(
                out, row, as_of=as_of, bars=frame, avg_cost=avg_cost,
                journal_sig=hsc.journal_buy_sig(trades_df, t),
                journal_readable=trades_df is not None,
                policy_sig_now=hsf.policy_sig(), carry_readable=readable)
        except Exception as e:      # a carry bug must never remove or lower today's stop
            kept = dict(out)
            kept["carry"] = hsc._carry_dict(
                hsc.FAILED, reason=f"{hsc.CARRY_CHECK_FAILED}: {type(e).__name__}",
                computed=_f(out.get("stop")),
                note=("The carried stop couldn't be checked, so this stop may be lower than "
                      "on earlier days."))
            return with_seen(kept)
        cs = res.get("carry")
        state = cs.get("state") if isinstance(cs, dict) else None
        if state == hsc.APPLIED:
            # the carried stop's own label: `label_for("prior_session", "trailing")`
            # would read "Prior-close ATR", which is false for a carried trailing stop
            res["label"] = LABEL_CARRIED
        if state in (hsc.APPLIED, hsc.NOT_BINDING) and NOTE_NO_TRAIL in str(res.get("note") or ""):
            res["note"] = str(res["note"]).replace(NOTE_NO_TRAIL, NOTE_NO_TRAIL_CARRIED)
        return with_seen(res)

    def tagged(out: dict) -> dict:
        """`out` (a copy) with the journal episode's status and reason, on every path."""
        r = dict(out)
        r["episode_status"] = ep.get("status")
        r["episode_reason"] = ep.get("reason")
        return r

    if ep.get("status") == "ok" and ep.get("start") is not None and ep["start"] >= as_of:
        # bought today: never floored or carried; `buy_day_path` lets a test pin that
        # (the existing `buy_day` key is False on the rejected-fill prior_session result)
        r = tagged(_buy_day(t, ep, bundle, avg_cost, as_of, pol, fetch_history, fetch_range))
        r["buy_day_path"] = True
        return r

    if ep.get("status") != "ok":
        # U6: the unadjusted 1-year frame FIRST (as buy-day does; its closes are the ones
        # the saved carry was written from), the 6-month bundle only when that fails.
        try:
            df = fetch_history(t, "1y")
        except Exception:
            df = None
        if not _usable_frame(df):
            df = bundle
        if not _usable_frame(df):
            return tagged(_unavailable(t, "No price history is available for this position.", avg_cost))
        res = run(df)
        return tagged(carried(floored(_wrap(t, res, _note_for(res, ep, bundle=False, n_sessions=None,
                                                               fetch_problem=None), avg_cost, ep=ep), df), df))

    long_df, problem = _long_frame(t, ep, as_of, pol, fetch_history)
    if long_df is not None and problem is None:
        res = run(long_df)
        return tagged(carried(floored(_wrap(t, res, _note_for(res, ep, bundle=False, n_sessions=None,
                                                               fetch_problem=None), avg_cost, ep=ep), long_df), long_df))

    # fallback: the app's bundle frame, always "partial"
    problem = problem or "Longer price history couldn't be loaded"
    frame = bundle if bundle is not None else long_df
    if frame is None:
        return tagged(_unavailable(t, problem + " and the app's own price history is missing.", avg_cost))
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
    return tagged(carried(floored(_wrap(t, res, note, avg_cost, fell_back=True, ep=ep), frame), frame))


def build_held_stops(holdings, held_data, trades_df, as_of: date, *, fetch_history,
                     floors, carry, fetch_range=None) -> dict:
    """{TICKER: held-stop result} for every valid holding. NEVER raises: a ticker
    whose build throws becomes a degraded "Stop Unavailable" result with a note.

    holdings: rows with a ticker, exact shares ("Shares Exact" / "Shares" /
    "shares") and average cost. held_data: {ticker: load_all result} (its "df" is
    the 6-month bundle frame). trades_df: the strict journal read (None =
    unreadable). as_of: ET `date` (always passed in). fetch_history(ticker,
    period) -> unadjusted OHLCV frame; may raise or return None. fetch_range(
    ticker, as_of) -> {"low", "high"} | None is optional and used only for a
    bought-today position when no frame has a today row (see `_buy_day`).
    floors: REQUIRED (no default, so a caller can't forget it): the saved held-stop
    floors, {TICKER: {episode_start: row}}; `{}` = read, none saved; `None` = couldn't
    be read (the floor stays off and a degraded note says so).
    carry: REQUIRED (no default, same reason): the saved carried stops, {TICKER: row};
    `{}` = read, none saved; `None` = couldn't be read (the carry stays off, the
    result's `carry.state` says "unreadable" and a degraded note says so)."""
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
            out[t] = _one(h, held_data, trades_df, as_of, fetch_history, pol, floors, carry,
                          fetch_range)
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


def plain_text_note(note) -> str:
    """A held-stop note for a plain-text / HTML-escaped surface (the cron email
    footer): the notes escape each dollar sign for Streamlit markdown, which would
    print a literal backslash there."""
    return str(note or "").replace(chr(92) + "$", "$")


def full_note(h) -> str:
    """A held-stop result's note plus its saved-floor sentence (`floor.note`, set
    only on a degraded basis; empty otherwise) and its carried-stop sentence
    (`carry.note`; empty when there is none), each joined by a space. Unchanged
    when there is neither."""
    if not isinstance(h, dict):
        return ""
    note = str(h.get("note") or "")
    for key in ("floor", "carry"):
        part = h.get(key)
        extra = str(part.get("note") or "") if isinstance(part, dict) else ""
        if extra:
            note = f"{note} {extra}".strip()
    return note


def degraded_notes(held_stops) -> list[tuple[str, str, str]]:
    """[(ticker, label, note)] for every degraded result, sorted by ticker: the
    rows of the degraded-basis banner. The note includes the saved-floor and carried-stop
    sentences (`full_note`) when there are any."""
    if not isinstance(held_stops, dict):
        return []
    return [(t, str(h.get("label") or ""), full_note(h))
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
