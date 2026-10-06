"""
R-Multiple — measure a closed round-trip trade episode's realized P&L against
the risk that WOULD have been planned for it.

Two lenses, never blended:
  "engine"   — the app's own ATR-based entry stop, reconstructed from price
               history alone (no DDL, no captured data needed). Available
               today.
  "declared" — the owner's own declared stop at entry time, read from a
               per-leg risk-plan attached to each BUY fill. Capture (chunk 3,
               `declared_entry_risk_plan`/`app.py`'s BUY-time write) and the
               wiring that carries it onto each leg (`ticker_history.py`'s
               `_build_episode`, fixed 2026-10-02 — audit finding H2; it
               previously never included the field, so this lens could never
               resolve regardless of captured data) are both done. No
               fabrication either way: a leg with no captured plan still
               resolves to `None`, this module never invents a declared stop.
               **Not yet surfaced anywhere in the UI** (both `app.py` and
               `performance_review.py` currently call this with
               `lens="engine"` only) — that's a display decision, not a data
               gap.

This is a MEASUREMENT module only: it computes an R-multiple and a few
factual flags, nothing else. It does not gate, score, recommend, or persist
anything, and it imports nothing from any `_GATE_FILES` module as a decision
dependency — `constants.ATR_STOP_MULT` is read as plain public reference
data, exactly as `scripts/exit_ladder_replay.py` already does.

Every public function fails open: bad/missing input returns `None` (or a
dict whose numeric fields are `None`), never an exception and never a
NaN leaking into a returned dict (NaN is not JSON-safe and would break a
future DB write that reuses these functions — defensive now, load-bearing
once a capture/persistence chunk lands).

No lookahead: every risk reconstruction uses only OHLC bars STRICTLY BEFORE
the date in question — a trade's own day's bar never influences its own
risk calculation. Mirrors the same discipline already used in
`scripts/exit_ladder_replay.py`.

Pure logic — no Streamlit, no DB calls, no network.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

import pandas as pd

from stock_analyzer.constants import ATR_STOP_MULT
from stock_analyzer.indicators import atr as _atr_series

# Minimum PRIOR bars required before a reconstructed ATR(14) is trusted as
# "real" rather than a thin/degenerate early-window value. Deliberately NOT
# using `risk._atr_value`'s mean(High-Low) fallback — this module always
# requires a genuine ATR(14), or returns None. Not an investment-policy
# threshold (nothing is gated on it) — purely a data-sufficiency floor for a
# measurement module, same category as `ticker_history._SHARES_ZERO_THRESHOLD`.
_MIN_PRIOR_BARS = 15
_ATR_LENGTH = 14

# Floating-point fuzz floor for "shares effectively zero" — same tolerance
# used throughout the codebase (ticker_history.py, portfolio.py, db.py).
_SHARES_ZERO_THRESHOLD = 1e-9


# ── Private helpers ────────────────────────────────────────────────────────

def _f(v: Any, default: float | None = None) -> float | None:
    """Safe float coercion; returns `default` on None / NaN / +-inf / non-numeric.
    +-inf is rejected for the same reason NaN is (Opus review, 2026-10-02):
    an infinite risk_dollars/risk_pct would serialize as invalid JSON
    (`Infinity`) and fail a future jsonb write whole-hog. Not reachable
    through today's UI inputs (a `st.number_input` price/shares can't be
    inf), but this module's own contract is "never emit a non-finite number",
    not "never emit one through inputs this module happens to see today"."""
    if v is None:
        return default
    try:
        x = float(v)
        return x if isfinite(x) else default   # NaN guard + +-inf guard
    except (TypeError, ValueError):
        return default


def _to_date(v: Any) -> date | None:
    """Coerce a Timestamp / string / date to a plain date; None on failure."""
    if v is None:
        return None
    try:
        if hasattr(v, "date"):
            return v.date()
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _safe(v: Any) -> Any:
    """JSON-safety: coerce NaN floats to None; pass everything else through."""
    if isinstance(v, float) and v != v:   # NaN guard
        return None
    return v


def _safe_dict(d: dict) -> dict:
    """Apply `_safe` to every value of a flat dict before returning it."""
    return {k: _safe(v) for k, v in d.items()}


def _prior_bars(ohlc_df: Any, as_of_date: date) -> pd.DataFrame | None:
    """OHLC rows strictly before `as_of_date`. None on any structural problem."""
    if ohlc_df is None or not hasattr(ohlc_df, "index"):
        return None
    if not hasattr(ohlc_df, "columns"):
        return None
    for col in ("High", "Low", "Close"):
        if col not in ohlc_df.columns:
            return None
    try:
        mask = [(d := _to_date(ts)) is not None and d < as_of_date for ts in ohlc_df.index]
    except Exception:
        return None
    if not any(mask):
        return None
    try:
        sub = ohlc_df.loc[mask]
    except Exception:
        return None
    if sub is None or len(sub) == 0:
        return None
    return sub


# ── Public API ──────────────────────────────────────────────────────────────

def engine_reference_risk(
    ohlc_df: Any, as_of_date: date, atr_mult: float = ATR_STOP_MULT
) -> dict | None:
    """
    The app's own stop-based risk-per-share AS OF `as_of_date`, using only
    OHLC bars strictly before that date (no lookahead — the trade's own
    day's bar never influences its own risk calc).

    Requires at least `_MIN_PRIOR_BARS` prior bars for a genuine ATR(14);
    returns None otherwise. Never falls back to a mean(High-Low) proxy —
    unlike `risk._atr_value`, which exists for a different, display-only
    purpose and must not be reused here.

    Returns
    -------
    None
        Insufficient history, bad input, or a non-finite result.
    dict
        {"risk_pct": float, "risk_per_share": float, "atr_mult_used": float,
         "bars_used": int, "atr_value": float, "ref_price": float}
        risk_pct is expressed in percent (not a 0-1 fraction), matching the
        rest of the codebase's "_pct" convention. risk_per_share and
        ref_price are both in the OHLC frame's own price units (dollars).
    """
    if as_of_date is None:
        return None
    try:
        mult = float(atr_mult)
    except (TypeError, ValueError):
        return None
    if mult != mult or mult <= 0:   # NaN or non-positive
        return None

    prior = _prior_bars(ohlc_df, as_of_date)
    if prior is None:
        return None
    n_bars = len(prior)
    if n_bars < _MIN_PRIOR_BARS:
        return None

    try:
        atr_vals = _atr_series(prior["High"], prior["Low"], prior["Close"], length=_ATR_LENGTH)
    except Exception:
        return None
    if atr_vals is None or len(atr_vals) == 0:
        return None
    atr_val = _f(atr_vals.iloc[-1])
    if atr_val is None or atr_val <= 0:
        return None

    ref_price = _f(prior["Close"].iloc[-1])
    if ref_price is None or ref_price <= 0:
        return None

    risk_per_share = mult * atr_val
    if risk_per_share != risk_per_share or risk_per_share <= 0:
        return None
    risk_pct = risk_per_share / ref_price * 100.0
    if risk_pct != risk_pct:
        return None

    return _safe_dict({
        "risk_pct":        risk_pct,
        "risk_per_share":  risk_per_share,
        "atr_mult_used":   mult,
        "bars_used":       n_bars,
        "atr_value":       atr_val,
        "ref_price":       ref_price,
    })


def build_risk_plan(
    entry_price: float,
    shares: float,
    ohlc_df: Any,
    as_of_date: date,
    declared_stop_price: float | None = None,
) -> dict | None:
    """
    Combine a share count + entry price with either a DECLARED stop price
    (if given) or the ENGINE reference risk (fallback) to produce a risk
    plan. Never raises; any bad/missing input returns None.

    A resolved stop at or above `entry_price` is not a valid risk (it would
    compute a zero/negative risk) — returns None rather than fabricate a
    nonsensical number.

    Returns
    -------
    None
        No resolvable, valid risk plan.
    dict
        {"risk_dollars": float, "stop_price": float, "source": "declared"|"engine",
         "atr_mult_used": float}  — atr_mult_used present only for source="engine".
    """
    entry = _f(entry_price)
    sh = _f(shares)
    if entry is None or entry <= 0:
        return None
    if sh is None or sh <= 0:
        return None
    if as_of_date is None:
        return None

    # ── Declared lens ────────────────────────────────────────────────────────
    declared = _f(declared_stop_price)
    if declared is not None:
        if declared <= 0 or declared >= entry:
            return None
        risk_dollars = (entry - declared) * sh
        if risk_dollars != risk_dollars or risk_dollars <= 0:
            return None
        return _safe_dict({
            "risk_dollars": risk_dollars,
            "stop_price":   declared,
            "source":       "declared",
        })

    # ── Engine lens (fallback) ──────────────────────────────────────────────
    engine = engine_reference_risk(ohlc_df, as_of_date, ATR_STOP_MULT)
    if engine is None:
        return None
    risk_per_share = engine["risk_per_share"]
    stop_price = entry - risk_per_share
    if stop_price >= entry or stop_price <= 0:
        return None
    risk_dollars = risk_per_share * sh
    if risk_dollars != risk_dollars or risk_dollars <= 0:
        return None
    return _safe_dict({
        "risk_dollars":   risk_dollars,
        "stop_price":     stop_price,
        "source":         "engine",
        "atr_mult_used":  engine["atr_mult_used"],
    })


def _declared_skip_reason(
    action: str | None,
    is_retrospective: bool,
    trigger_direction: str | None,
    trigger_price: float | None,
) -> str | None:
    """
    Single source of truth for `declared_entry_risk_plan`'s four early-return
    checks. Returns a reason code if the capture gate must skip (never build
    a plan at all), or None if none of the checks fire (meaning: proceed to
    try `build_risk_plan`).

    Reason codes
    ------------
    "not_buy"               `action` is not "BUY" (SELL/SPLIT never get a
                            risk plan here).
    "retrospective_entry"   `is_retrospective` is True — a broker-sourced or
                            manually-backdated entry is logged after the
                            fact, so a stop typed in hindsight is not a real
                            ahead-of-time commitment.
    "no_downside_trigger"   `trigger_direction` isn't exactly "below" —
                            covers "no trigger extracted", "not_checkable",
                            and any other direction/shape.
    "invalid_trigger_price" `trigger_price` is non-numeric / NaN / None —
                            rejected here, before ever reaching
                            `build_risk_plan`.

    Never raises.
    """
    if action != "BUY":
        return "not_buy"
    if is_retrospective:
        return "retrospective_entry"
    if trigger_direction != "below":
        return "no_downside_trigger"
    if _f(trigger_price) is None:
        return "invalid_trigger_price"
    return None


def declared_entry_risk_plan(
    action: str | None,
    is_retrospective: bool,
    trigger_direction: str | None,
    trigger_price: float | None,
    entry_price: float,
    shares: float,
    as_of_date: date,
) -> dict | None:
    """
    Chunk 3 capture gate: decide whether a BUY gets a declared risk plan at
    all, using the owner's existing Pre-Mortem price trigger as the
    "declared stop" (D1 = option A, owner-confirmed) — no other input is
    treated as a declared stop.

    None (no plan attached) whenever:
      * `action` is not "BUY" (SELL/SPLIT never get a risk plan here), or
      * `is_retrospective` is True — a broker-sourced or manually-backdated
        entry is logged after the fact, so a stop typed in hindsight is not
        a real ahead-of-time commitment, or
      * `trigger_direction` isn't exactly `"below"` — covers "no trigger
        extracted", `"not_checkable"`, and any other direction/shape, or
      * the underlying `build_risk_plan` call itself returns None (e.g. the
        trigger price is non-numeric, non-positive, or at/above entry).

    (These four checks are delegated to `_declared_skip_reason` — the single
    source of truth also used by `declared_entry_risk_plan_with_reason`.)

    `ohlc_df` is deliberately never passed through to `build_risk_plan` (it's
    always called with `ohlc_df=None`) — this keeps the result strictly on
    the declared lens; the engine/ATR fallback must never fire here, which
    would silently turn a genuine "no declared stop" case into a different
    kind of plan. This isolation is enforced TWICE, deliberately: a missing/
    non-numeric trigger price is rejected directly (never handed to
    `build_risk_plan` at all), AND the result is re-checked for
    `source == "declared"` before being returned. Today `ohlc_df=None` alone
    is enough to make `build_risk_plan`'s engine fallback return None (no
    price history to compute an ATR from) — but that's an accident of
    `engine_reference_risk`'s current behaviour, not a guarantee this
    function makes on its own. If a future change ever gives
    `engine_reference_risk` some other fallback (it lives outside
    `_GATE_FILES`, so such a change wouldn't be review-gated the way this
    capture path is), this function must still never let an engine-lens plan
    masquerade as a declared one (Opus review finding, 2026-10-02).

    An ADD (a BUY on an already-held ticker) is just another call with that
    leg's own entry/shares/date — it always gets its own fresh plan here,
    never a prior leg's.

    Never raises — any bad/missing input flows through to `build_risk_plan`'s
    own None-safe guards, or is rejected directly above.
    """
    if _declared_skip_reason(action, is_retrospective, trigger_direction, trigger_price) is not None:
        return None
    plan = build_risk_plan(
        entry_price=entry_price,
        shares=shares,
        ohlc_df=None,
        as_of_date=as_of_date,
        declared_stop_price=trigger_price,
    )
    if plan is None or plan.get("source") != "declared":
        return None
    return plan


def declared_entry_risk_plan_with_reason(
    action: str | None,
    is_retrospective: bool,
    trigger_direction: str | None,
    trigger_price: float | None,
    entry_price: float,
    shares: float,
    as_of_date: date,
) -> tuple[dict | None, str | None]:
    """
    Same capture gate as `declared_entry_risk_plan`, but also reports WHY a
    plan was skipped — distinguishing an expected, legitimate skip from a
    crash, so a caller can store the reason as a diagnostic alongside
    `risk_plan=None` instead of collapsing both into an identical blob.

    Returns
    -------
    (dict, None)
        A usable declared risk plan — identical to what
        `declared_entry_risk_plan` would return for the same inputs.
    (None, str)
        No plan, with one of these reason codes:
          "not_buy"               — see `_declared_skip_reason`.
          "retrospective_entry"   — see `_declared_skip_reason`.
          "no_downside_trigger"   — see `_declared_skip_reason`.
          "invalid_trigger_price" — see `_declared_skip_reason`.
          "risk_calc_failed"      — none of the four checks above fired, but
                                    `build_risk_plan` itself could not
                                    resolve a usable declared plan (e.g. the
                                    trigger price is non-positive or at/above
                                    entry) — passed the gate, failed the math.

    Never raises — mirrors `declared_entry_risk_plan`'s own None-safe
    contract; the caller is still expected to guard against unexpected
    exceptions from upstream inputs (e.g. a malformed `as_of_date`) with its
    own try/except, same as it does around `declared_entry_risk_plan` today.
    """
    reason = _declared_skip_reason(action, is_retrospective, trigger_direction, trigger_price)
    if reason is not None:
        return None, reason
    plan = build_risk_plan(
        entry_price=entry_price,
        shares=shares,
        ohlc_df=None,
        as_of_date=as_of_date,
        declared_stop_price=trigger_price,
    )
    if plan is not None and plan.get("source") == "declared":
        return plan, None
    return None, "risk_calc_failed"


def episode_r_multiple(episode: dict, lens: str, ohlc_df: Any) -> dict:
    """
    R-multiple for one CLOSED episode (shape from `ticker_history`'s
    `_build_episode` — see that module for the authoritative field names;
    this function does not reconstruct episodes itself).

    lens = "engine"   — computes `engine_reference_risk` independently for
                        EACH buy leg (as-of that leg's own trade date), sums
                        risk dollars across legs.
    lens = "declared" — expects per-leg risk-plan dicts already attached to
                        each buy leg (capture + wiring both shipped as of
                        2026-10-02 — see module docstring; a leg with no
                        captured plan still resolves to "no plan" honestly,
                        never fabricated).

    All-or-nothing: partial leg coverage NEVER produces a partial/approximate
    number — if any leg's risk can't be resolved, the whole episode's
    R-multiple is None.

    Returns
    -------
    dict
        {"r_multiple": float, "lens": str, "legs_planned": int, "legs_total": int}
        when every leg has a resolvable risk.
        {"r_multiple": None, "lens": str, "reason": str,
         "legs_planned": int, "legs_total": int}
        otherwise (open episode, no priced legs, any unplanned leg, missing
        price history, or a non-finite result).
    """
    if not isinstance(episode, dict):
        return {"r_multiple": None, "lens": lens, "reason": "invalid episode",
                "legs_planned": 0, "legs_total": 0}

    if episode.get("status") != "closed":
        return {"r_multiple": None, "lens": lens, "reason": "episode still open",
                "legs_planned": 0, "legs_total": 0}

    fills = episode.get("fills")
    if fills is None:
        fills = []
    buys = [f for f in fills if isinstance(f, dict) and f.get("action") == "BUY"]
    legs_total = len(buys)
    if legs_total == 0:
        return {"r_multiple": None, "lens": lens, "reason": "no priced legs",
                "legs_planned": 0, "legs_total": 0}

    total_risk_dollars = 0.0
    legs_planned = 0

    for b in buys:
        entry_price = _f(b.get("price"))
        shares = _f(b.get("shares"))
        as_of_date = _to_date(b.get("date"))

        plan: dict | None = None
        if lens == "declared":
            # Not invented here — only reused if a capture step already
            # attached one to this leg. Capture (app.py's BUY-time write)
            # and the wiring onto each fill (ticker_history._build_episode,
            # fixed 2026-10-02) are both live; this still reads None for any
            # leg where no plan was actually captured.
            attached = b.get("risk_plan")
            if isinstance(attached, dict) and attached.get("source") == "declared":
                rd = _f(attached.get("risk_dollars"))
                if rd is not None and rd > 0:
                    plan = attached
        elif lens == "engine":
            if entry_price is not None and shares is not None and as_of_date is not None:
                plan = build_risk_plan(entry_price, shares, ohlc_df, as_of_date, declared_stop_price=None)
        else:
            return {"r_multiple": None, "lens": lens, "reason": f"unknown lens '{lens}'",
                     "legs_planned": 0, "legs_total": legs_total}

        if plan is None:
            continue
        rd = _f(plan.get("risk_dollars"))
        if rd is None or rd <= 0:
            continue
        legs_planned += 1
        total_risk_dollars += rd

    if legs_planned < legs_total:
        reason = ("no declared risk plan captured for any leg"
                  if (lens == "declared" and legs_planned == 0)
                  else "not every leg has a resolvable risk")
        return {"r_multiple": None, "lens": lens, "reason": reason,
                "legs_planned": legs_planned, "legs_total": legs_total}

    if total_risk_dollars <= 0 or total_risk_dollars != total_risk_dollars:
        return {"r_multiple": None, "lens": lens, "reason": "zero total planned risk",
                "legs_planned": legs_planned, "legs_total": legs_total}

    realized_pnl = _f(episode.get("realized_pnl"))
    if realized_pnl is None:
        return {"r_multiple": None, "lens": lens, "reason": "realized P&L unavailable",
                "legs_planned": legs_planned, "legs_total": legs_total}

    r_multiple = realized_pnl / total_risk_dollars
    if r_multiple != r_multiple:   # NaN guard
        return {"r_multiple": None, "lens": lens, "reason": "non-finite result",
                "legs_planned": legs_planned, "legs_total": legs_total}

    return _safe_dict({
        "r_multiple":   r_multiple,
        "lens":         lens,
        "legs_planned": legs_planned,
        "legs_total":   legs_total,
    })


def add_flags(episode: dict) -> dict:
    """
    Pure, factual flags about an episode's own buy legs — no constants
    needed, nothing gated.

    Returns
    -------
    dict
        {"added_while_losing": bool} — True iff any BUY leg after the first
        was priced below the running weighted-average cost at the time of
        that add (i.e. "averaging down" happened at least once).
    """
    if not isinstance(episode, dict):
        return {"added_while_losing": False}

    fills = episode.get("fills")
    if fills is None:
        fills = []
    buys = [f for f in fills if isinstance(f, dict) and f.get("action") == "BUY"]

    added_while_losing = False
    running_shares = 0.0
    running_cost = 0.0
    for i, b in enumerate(buys):
        shares = _f(b.get("shares"), 0.0)
        price = _f(b.get("price"), 0.0)
        if i > 0 and running_shares > _SHARES_ZERO_THRESHOLD:
            avg_cost = running_cost / running_shares
            if price is not None and price < avg_cost:
                added_while_losing = True
        running_shares += shares or 0.0
        running_cost += (shares or 0.0) * (price or 0.0)

    return {"added_while_losing": added_while_losing}
