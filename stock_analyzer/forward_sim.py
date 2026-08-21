"""
Forward Portfolio Simulator (E1, Phase 1) — replay the app's OWN mechanical
rules against a shocked book and report the surviving portfolio.

The gap this closes: `stress_test.py` shocks prices and reports P&L, and it
contains no stop or gate logic at all. F-224 Outcome Range is a per-name
bootstrap distribution. Neither answers "after MY rules fire, what am I left
holding?" — which is the only question that tests the *interaction* of ~20
gates, the deterioration ladder and the stop ladder, all of which were set as
isolated policy decisions.

FAITHFUL BY CONSTRUCTION — the single most important property here. Every tier
decision comes from `exit_advisor.classify_deterioration_tier` (the same pure
scalar core the Daily Brief uses); the risk-off overlay comes from
`exit_advisor.risk_off_regime` + `assess_risk_off_derisk` verbatim; the shock
model comes from `stress_test.run_scenario`. This module only does the *scalar
extraction* at a substituted price — it never re-implements a tier rule. The
`test_forward_sim.py` zero-shock identity test pins that extraction against
`exit_advisor.assess_holding` so it cannot drift.

Pure logic — no Streamlit, no I/O, no DB. Read-only: writes nothing, gates
nothing, changes no recommendation. Awareness/diagnostic only.

WHAT IS DERIVED vs ASSUMED (state this honestly in any UI):
  Derived from real data — the shocked price (existing beta/sector shock model),
  the high-water peak, the trend MA and the trend break, relative strength (the
  engine's REAL trailing RS *plus* the scenario differential — additive, never a
  replacement; see `replay_position`), the ratcheted protective stop, whether
  risk-off arms (SPY's shocked close vs its real 200-day mean — the two legs are
  OR'd, so no VIX assumption is needed), and book fragility (a property of the
  book, not of the shock, so today's value is the honest input).
  ASSUMED — exactly one thing: `below_ma_count`. See `TIER_CONFIRMED` below.

See docs/plans/forward-portfolio-simulator.md.
"""

from __future__ import annotations

from stock_analyzer import exit_advisor, stress_test
from stock_analyzer.constants import (
    DETERIORATION_CONFIRM_DAYS,
    DETERIORATION_TREND_MA,
    GAP_TO_STOP_ROUND_DECIMALS,
    REL_STRENGTH_LOOKBACK_DAYS,
)

# The two reads rendered side by side. Their DIFFERENCE is the finding: it
# quantifies how much of the book's protection depends on confirmation lag.
TIER_DAY1 = "day1"           # below_ma_count from REAL pre-shock history.
TIER_CONFIRMED = "confirmed"  # below_ma_count = DETERIORATION_CONFIRM_DAYS.

# Why two: `classify_deterioration_tier` requires 2-of-3 sessions below the
# trend MA before TRIM can activate. On day 1 of a shock that genuinely has not
# happened, so only the deep-drawdown EXIT shortcut fires — that is the honest
# immediate answer, not a bug. The CONFIRMED read models the multi-week duration
# the scenarios describe. Neither is "the" answer; the bracket is.


def _as_dict(value):
    """Coerce to dict without the `.get(...) or {}` offline-sentinel pattern."""
    return value if isinstance(value, dict) else {}


def _num(value, default=None):
    """float() or `default` — also rejects NaN (so a float64 NaN cell is None)."""
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:   # NaN
        return default
    return out


def _peak(close, age_days, peak_window_days):
    """High-water mark over the holding window.

    Window math is SHARED with `assess_holding` via `exit_advisor
    ._peak_window_bars` rather than duplicated, so it cannot drift.
    """
    window = peak_window_days if peak_window_days is not None else age_days
    peak = float(close.tail(exit_advisor._peak_window_bars(window)).max())
    return peak if peak > 0 else None


def _live_rel_strength(close, spy_df):
    """The engine's real trailing relative strength in pct points, or None.

    Byte-for-byte the calculation in `assess_holding` (same `_pct_return` over
    `REL_STRENGTH_LOOKBACK_DAYS`, same `_series_close` on the benchmark).

    Returns **None** rather than the engine's 0.0 when either leg is missing, so
    the caller can record that the trailing reading was unavailable. That matters
    because the engine's 0.0 is a fail-safe — an unknown RS must never open an
    action tier — and that guarantee does NOT survive composition here: 0.0 plus
    a negative scenario differential is still negative, so a degraded benchmark
    fetch could open a TRIM the engine would have withheld. Rare, but it is the
    one unknown in this module that would otherwise go unnamed.
    """
    name_ret = exit_advisor._pct_return(close, REL_STRENGTH_LOOKBACK_DAYS)
    spy_ret = exit_advisor._pct_return(
        exit_advisor._series_close(spy_df), REL_STRENGTH_LOOKBACK_DAYS
    )
    if name_ret is None or spy_ret is None:
        return None
    return (name_ret - spy_ret) * 100.0


def _below_ma_count(close, ma):
    """Sessions below the trend MA in the confirmation window (real history)."""
    k = DETERIORATION_CONFIRM_DAYS
    count = 0
    for c, m in zip(close.tail(k).tolist(), ma.tail(k).tolist()):
        if m == m and c == c and c < m:   # skip NaN bars, same as assess_holding
            count += 1
    return count


def replay_position(
    *,
    ticker,
    df,
    price_now,
    move_pct,
    spy_move,
    atr,
    avg_cost,
    shares,
    stop,
    spy_df=None,
    age_days=None,
    peak_window_days=None,
):
    """Replay one position's stop + deterioration outcome at the shocked price.

    Returns a dict, or None when there is not enough real history to judge
    (missing Close, missing/NaN trend MA, non-positive peak). Returning None is
    deliberate: a fabricated tier on absent data is worse than an honest gap,
    and the caller reports these as uncovered rather than as "safe".
    """
    price_now = _num(price_now)
    move_pct = _num(move_pct)
    if price_now is None or price_now <= 0 or move_pct is None:
        return None

    close = exit_advisor._series_close(df)
    if close is None:
        return None

    ma_col = f"SMA_{DETERIORATION_TREND_MA}"
    if ma_col not in getattr(df, "columns", []):
        return None
    ma = df[ma_col]
    sma_now = _num(ma.iloc[-1])
    if sma_now is None:
        return None

    shocked = price_now * (1.0 + move_pct / 100.0)
    if shocked <= 0:
        return None

    peak = _peak(close, age_days, peak_window_days)
    if peak is None:
        return None

    # ── Scalars at the shocked price ──────────────────────────────────────────
    dd_from_peak_pct = max(0.0, (peak - shocked) / peak * 100.0)
    atr_v = _num(atr)
    # ATR is a dollar volatility measure; expressing it against the SHOCKED
    # price is the internally-consistent choice (the ATR-scaled TRIM/EXIT floors
    # then widen as the price falls, exactly as they would live).
    atr_pct = (atr_v / shocked * 100.0) if atr_v else 0.0
    trend_broken_now = shocked < sma_now

    # Relative strength = the engine's REAL trailing RS, plus the scenario's own
    # name-vs-SPY differential. It MUST be additive, not a replacement.
    #
    # A first cut used the differential alone. That was a real defect (caught in
    # review): for the 6 of 9 sector-targeted scenarios, any holding whose sector
    # is absent from `_SECTOR_SHOCKS` gets `est_move = 0.0`, so the differential
    # alone reads `0 − (−10) = +10` — a fabricated POSITIVE relative strength
    # that switches `trim_active` off on a name whose price hasn't moved and
    # whose real drawdown is unchanged. A name the Brief is calling TRIM/EXIT
    # right now would have rendered here as a mere WATCH, in the same session.
    # Same failure on any β<1 name in a broad scenario. False comfort, on the
    # feature's headline number.
    #
    # Additive is also what makes the zero-shock identity test bind on RS at
    # all: at 0% it reduces exactly to the engine's own value.
    live_rs = _live_rel_strength(close, spy_df)
    rel_strength = (live_rs if live_rs is not None else 0.0) + (
        move_pct - (_num(spy_move, 0.0) or 0.0)
    )

    avg_cost_v = _num(avg_cost, shocked)
    shares_v = _num(shares, 0.0) or 0.0
    dollar_pnl = (shocked - avg_cost_v) * shares_v

    below_now = _below_ma_count(close, ma)

    def _tier(below_ma_count):
        return exit_advisor.classify_deterioration_tier(
            dd_from_peak_pct=dd_from_peak_pct,
            atr_pct=atr_pct,
            trend_broken_now=trend_broken_now,
            below_ma_count=below_ma_count,
            rel_strength=rel_strength,
            price=shocked,
            avg_cost=avg_cost_v,
            dollar_pnl=dollar_pnl,
            age_days=age_days,
        )

    # ── Stop breach ───────────────────────────────────────────────────────────
    # `stop` MUST be port_df["Stop"] — the RATCHETED/manual protective stop the
    # Brief acts on (max(ATR stop, ratchet floor), or a tighter manual). The raw
    # ATR bundle stop under-reports it once a profit tier engages, and on the
    # Analysis page it is overwritten to the manual price. A missing stop is a
    # data gap (G-11), never "no breach" — it is reported separately.
    stop_v = _num(stop)
    if stop_v is None or stop_v <= 0:
        stop_breached, gap_shocked = None, None
    else:
        # The Brief's exact test, same rounding, so the two agree.
        gap_shocked = round((shocked - stop_v) / shocked * 100, GAP_TO_STOP_ROUND_DECIMALS)
        stop_breached = gap_shocked <= 0

    return {
        "ticker": ticker,
        "price_now": round(price_now, 2),
        "price_shocked": round(shocked, 2),
        "move_pct": round(move_pct, 1),
        "peak": round(peak, 2),
        "dd_from_peak_pct": round(dd_from_peak_pct, 1),
        "atr_pct": round(atr_pct, 1),
        "sma": round(sma_now, 2),
        "trend_ma": DETERIORATION_TREND_MA,
        "trend_broken_now": trend_broken_now,
        "rel_strength": round(rel_strength, 1),
        "rel_strength_live": live_rs is not None,
        "below_ma_count_now": below_now,
        "shares": shares_v,
        "avg_cost": round(avg_cost_v, 2),
        "dollar_pnl_shocked": round(dollar_pnl, 0),
        "stop": round(stop_v, 2) if stop_v else None,
        "stop_available": stop_v is not None and stop_v > 0,
        "stop_breached": stop_breached,
        "gap_to_stop_shocked": gap_shocked,
        TIER_DAY1: _tier(below_now),
        TIER_CONFIRMED: _tier(DETERIORATION_CONFIRM_DAYS),
    }


def shock_spy_frame(spy_trend_df, spy_move):
    """Copy of the SPY frame with its FINAL close repriced by `spy_move`.

    Feeds `exit_advisor.risk_off_regime` verbatim, which compares the last close
    to `close.tail(trend_ma).mean()`. Only the final bar is moved: that models
    the terminal state, and the mean only drops by ~1/200th of the move while
    the last close drops by all of it — the gap widens, so the
    trend break can never be UNDERstated (a real multi-week decline would also
    drag the mean down slightly, making the break marginally harder to trip).
    Directionally protective, and stated rather than hidden.

    Returns None when there is no usable Close column — the caller then reports
    the risk-off overlay as unavailable rather than assuming it is unarmed.
    """
    if spy_trend_df is None or getattr(spy_trend_df, "empty", True):
        return None
    if "Close" not in getattr(spy_trend_df, "columns", []):
        return None
    move = _num(spy_move, 0.0) or 0.0
    out = spy_trend_df.copy()
    last_close = _num(out["Close"].iloc[-1])
    if last_close is None or last_close <= 0:
        return None
    out.iloc[-1, out.columns.get_loc("Close")] = last_close * (1.0 + move / 100.0)
    return out


def shock_port_df(port_df, moves):
    """Copy of port_df repriced by per-ticker `moves` (%), weights renormalised.

    Needed so `assess_risk_off_derisk` ranks β × weight on POST-shock weights
    (its ranking is weight-sensitive). Rows absent from `moves` are left
    untouched — `run_scenario` skips non-positive market values, and inventing a
    move for them would make a degraded book look falsely benign.
    """
    if port_df is None or getattr(port_df, "empty", True):
        return port_df
    out = port_df.copy()
    for idx, row in out.iterrows():
        ticker = str(row.get("Ticker", "")).upper()
        move = _num(moves.get(ticker)) if moves else None
        price = _num(row.get("Price"))
        if move is None or price is None or price <= 0:
            continue
        shocked = price * (1.0 + move / 100.0)
        shares = _num(row.get("Shares"), 0.0) or 0.0
        avg_cost = _num(row.get("Avg Cost"))
        out.at[idx, "Price"] = round(shocked, 2)
        out.at[idx, "Market Value"] = round(shocked * shares, 2)
        if avg_cost and avg_cost > 0:
            out.at[idx, "P&L ($)"] = round((shocked - avg_cost) * shares, 2)
            out.at[idx, "P&L (%)"] = round((shocked - avg_cost) / avg_cost * 100, 2)
    total = float(out["Market Value"].sum()) if "Market Value" in out.columns else 0.0
    if total > 0:
        out["Weight (%)"] = out["Market Value"] / total * 100.0
    return out


def mean_pairwise_corr(tickers, corr_df):
    """Mean pairwise correlation across `tickers`, or None.

    The headline finding this feature exists to surface: if the names that all
    stop out together are highly correlated, a book of N positions was really a
    handful of bets wearing N tickers. Returns None (never 0.0) when fewer than
    two tickers are resolvable — a missing correlation read must not render as
    "uncorrelated".

    Uses the codebase's established guard: check membership in BOTH index and
    columns, and reject a non-scalar `.loc` result, because duplicate labels in
    corr_df make `.loc[a, b]` return a frame rather than a number (pinned by
    tests/test_portfolio_intelligence.py).
    """
    if corr_df is None or getattr(corr_df, "empty", True):
        return None
    names = [str(t).upper() for t in (tickers or [])]
    values = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a not in corr_df.index or b not in corr_df.columns:
                continue
            try:
                cell = corr_df.loc[a, b]
            except (KeyError, TypeError):
                continue
            value = _num(cell)      # None for a frame/Series or a NaN cell
            if value is not None:
                values.append(value)
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _survivors(positions, which, held_data):
    """Aggregate the book left after the mechanical exits in `which` fire.

    An exit = a breached protective stop OR an EXIT tier. A TRIM is deliberately
    NOT liquidated here: its size is a per-card directive, so modelling it as a
    fixed haircut would invent a number. TRIMs are counted and reported instead.
    """
    kept, exited = [], []
    for p in positions:
        if p.get("stop_breached") is True or p.get(which) == exit_advisor.EXIT:
            exited.append(p)
        else:
            kept.append(p)

    def _mv(p):
        return (p.get("price_shocked") or 0.0) * (p.get("shares") or 0.0)

    kept_value = sum(_mv(p) for p in kept)
    proceeds = sum(_mv(p) for p in exited)
    post_shock_equity = kept_value + proceeds

    # Surviving beta = Σ(βᵢ × wᵢ) over survivors, renormalised to the surviving
    # book. None when no survivor carries a beta — never a fabricated 1.0.
    beta_num, beta_den = 0.0, 0.0
    for p in kept:
        rm = _as_dict(_as_dict(held_data.get(p["ticker"]) if held_data else None).get("risk_metrics"))
        beta = _num(rm.get("beta"))
        if beta is None:
            continue
        beta_num += beta * _mv(p)
        beta_den += _mv(p)
    surviving_beta = round(beta_num / beta_den, 2) if beta_den > 0 else None

    sectors: dict[str, float] = {}
    for p in kept:
        if kept_value > 0:
            sectors[p.get("sector", "Other")] = round(
                sectors.get(p.get("sector", "Other"), 0.0) + _mv(p) / kept_value * 100.0, 1
            )

    return {
        "n_kept": len(kept),
        "n_exited": len(exited),
        "exited_tickers": [p["ticker"] for p in exited],
        "kept_value": round(kept_value, 0),
        "proceeds": round(proceeds, 0),
        # Proceeds as a share of post-shock EQUITY. Deliberately not compared to
        # the regime cash floor: that comparison implies a redeployment
        # directive, which is Phase 2 and needs its own policy conversation.
        "proceeds_pct": round(proceeds / post_shock_equity * 100, 1) if post_shock_equity > 0 else None,
        "surviving_beta": surviving_beta,
        "surviving_sectors": dict(sorted(sectors.items(), key=lambda kv: -kv[1])),
    }


def simulate(
    scenario,
    port_df,
    held_data,
    *,
    spy_df=None,
    spy_trend_df=None,
    vix_level=None,
    fragility=None,
    portfolio_beta=None,
    custom_spy_move=None,
):
    """Run one scenario end-to-end. Returns None when the book can't be shocked.

    `held_data[t]` supplies `df` (indicator frame), `atr`, `position_age_days`,
    `material_add_age_days` and `risk_metrics.beta` — the same inputs
    `daily_briefing.deterioration_signals` already passes to `assess_holding`,
    so this reads the book the way the Brief does.
    """
    shock = stress_test.run_scenario(
        scenario, port_df, held_data, portfolio_beta, custom_spy_move
    )
    if not shock:
        return None

    spy_move = shock.get("spy_move")
    moves = {str(r["Ticker"]).upper(): r["Est. Move (%)"] for r in shock.get("rows", [])}
    sectors = {str(r["Ticker"]).upper(): r.get("Sector", "Other") for r in shock.get("rows", [])}

    positions, uncovered, no_value = [], [], []
    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "")).upper()
        if not ticker:
            continue
        if ticker not in moves:
            # run_scenario skipped it (non-positive market value — usually a
            # missing price). Its own bucket, not silently absent from every
            # count: a holding the sim can't see is a gap, not a non-holding.
            no_value.append(ticker)
            continue
        data = _as_dict(held_data.get(ticker) if held_data else None)
        result = replay_position(
            ticker=ticker,
            df=data.get("df"),
            price_now=row.get("Price"),
            move_pct=moves[ticker],
            spy_move=spy_move,
            atr=data.get("atr"),
            avg_cost=row.get("Avg Cost"),
            shares=row.get("Shares"),
            stop=row.get("Stop"),
            spy_df=spy_df,
            age_days=data.get("position_age_days"),
            peak_window_days=data.get("material_add_age_days"),
        )
        if result is None:
            uncovered.append(ticker)
            continue
        result["sector"] = sectors.get(ticker, "Other")
        positions.append(result)

    def _counts(which):
        out = {exit_advisor.WATCH: 0, exit_advisor.TRIM: 0, exit_advisor.EXIT: 0}
        for p in positions:
            tier = p.get(which)
            if tier in out:
                out[tier] += 1
        return out

    # ── Risk-off overlay, replayed on the shocked book ───────────────────────
    shocked_spy = shock_spy_frame(spy_trend_df, spy_move)
    # `fragility` is the outer AND-gate on the overlay, and it is a publish/
    # consume cache that is None when its PRODUCER FAILED — not when the book is
    # calm. Collapsing those two would make an offline read render as the benign
    # "would not arm", which is precisely the sentinel-collapse class this app
    # treats as a defect. Tracked separately so the UI can say "unknown".
    frag_ok = (
        isinstance(fragility, dict)
        and fragility.get("severity") in ("calm", "caution", "fragile")
    )
    risk_off = {
        "available": shocked_spy is not None,
        "fragility_available": frag_ok,
        "armed": False, "reasons": [], "cards": [],
    }
    if shocked_spy is not None:
        armed, reasons = exit_advisor.risk_off_regime(
            shocked_spy, vix_level,
            trend_ma=exit_advisor.RISK_OFF_TREND_MA,
            vix_threshold=exit_advisor.RISK_OFF_VIX_LEVEL,
        )
        risk_off["armed"] = armed
        risk_off["reasons"] = reasons
        if armed and frag_ok:
            # Exclude EVERY name already carrying any mechanical flag, in either
            # column. The live path passes decision_bucket.all_flagged_tickers()
            # — every ticker with a card in Act Today OR Review, deliberately
            # INCLUDING WATCH — because the 2026-07-29 audit (H6) found that a
            # narrower TRIM-only filter let a WATCH card and a same-render
            # risk-off "Trim now" card coexist for one ticker. A TRIM/EXIT-only
            # filter here would have re-opened exactly that contradiction.
            already = {
                p["ticker"] for p in positions
                if p.get("stop_breached") is True
                or p.get(TIER_DAY1) is not None
                or p.get(TIER_CONFIRMED) is not None
            }
            risk_off["cards"] = exit_advisor.assess_risk_off_derisk(
                shock_port_df(port_df, moves), held_data,
                fragility=fragility, spy_trend_df=shocked_spy,
                vix_level=vix_level, exclude_tickers=already,
            )

    stop_outs = [p["ticker"] for p in positions if p.get("stop_breached") is True]
    stop_missing = [p["ticker"] for p in positions if not p.get("stop_available")]
    # Names whose TRAILING relative strength couldn't be read (no benchmark, or
    # too little history). Their tier still resolves off the scenario
    # differential alone, which means the engine's "unknown RS never opens an
    # action tier" fail-safe doesn't hold for them — so they are named, like
    # every other unknown here, rather than blending in silently.
    rs_degraded = [p["ticker"] for p in positions if not p.get("rel_strength_live")]

    return {
        "scenario_id": scenario.get("id"),
        "scenario_label": scenario.get("label"),
        "spy_move": spy_move,
        "portfolio_value": shock.get("portfolio_value"),
        "post_shock_value": shock.get("post_shock_value"),
        "estimated_port_move": shock.get("estimated_port_move"),
        "positions": positions,
        "counts": {TIER_DAY1: _counts(TIER_DAY1), TIER_CONFIRMED: _counts(TIER_CONFIRMED)},
        "stop_outs": stop_outs,
        "stop_unavailable": stop_missing,
        "risk_off": risk_off,
        "survivors": {
            TIER_DAY1: _survivors(positions, TIER_DAY1, held_data),
            TIER_CONFIRMED: _survivors(positions, TIER_CONFIRMED, held_data),
        },
        # Honest coverage: names the sim could NOT judge, split by reason.
        # Never silently dropped, never counted as safe.
        "uncovered": uncovered,          # no usable price history / trend MA
        "no_value": no_value,            # no live market value to shock
        "rel_strength_degraded": rs_degraded,   # trailing RS leg unreadable
        "n_positions": len(positions),
    }
