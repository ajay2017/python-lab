"""
Held-position deterioration exit — the missing middle layer between "Hold" and a
score-collapse "Sell (<30)".

A trade-log review found the realized loss lived almost entirely in positions the
app never flagged: the composite sat inside Hold (44–64) while the name bled, so
no mechanical exit fired and the user bailed manually (on trend). This module
issues a graduated, drawdown-from-peak + trend-break signal so the app gets ahead
of that bleed:

    WATCH  → awareness only (Review lane). Down ≥ WATCH_DD% from peak AND below the
             trend MA. No action demanded.
    TRIM   → Act Today. Down past an ATR-scaled floor, below the MA for 2 of the
             last 3 sessions, AND weaker than the benchmark (idiosyncratic, not a
             market-wide down day — that's Phase 2's job).
    EXIT   → Act Today, reduce aggressively. TRIM conditions plus an escalation
             (underwater vs cost, large $ loss, or a deep drawdown). A DEEP
             drawdown fires EXIT on its own — depth IS confirmation, so a one-day
             gap-down doesn't wait for "2 of 3 below MA".

Design notes:
- The drawdown floors are ATR-scaled (a quiet name trips tight, a jumpy one gets
  room) but CAPPED by a ceiling so volatility can never disable the stop on the
  high-beta names that cause the biggest losses.
- The relative-strength filter gates only the ACTION tiers (TRIM/EXIT), not the
  awareness WATCH.
- Settling grace silences routine WATCH/TRIM on a freshly-opened position, but a
  deep EXIT is danger and is NEVER silenced by age (mirrors
  position_lifecycle.classify_position_state precedence).

Pure logic — no Streamlit / no I/O. The scalar decision core takes primitives so
it is trivially unit-testable; the pandas extraction layer pulls those primitives
from the same df / port_df / held_data the rest of the brief already has.

See docs/plans/exit-discipline.md.
"""

from __future__ import annotations

from stock_analyzer.constants import (
    DETERIORATION_WATCH_DD_PCT,
    DETERIORATION_TRIM_DD_PCT,
    DETERIORATION_EXIT_DD_PCT,
    DETERIORATION_ATR_MULT_TRIM,
    DETERIORATION_ATR_MULT_EXIT,
    DETERIORATION_TRIM_DD_CEILING,
    DETERIORATION_EXIT_DD_CEILING,
    DETERIORATION_EXIT_DOLLAR_LOSS,
    DETERIORATION_TREND_MA,
    DETERIORATION_CONFIRM_DAYS,
    DETERIORATION_CONFIRM_REQUIRED,
    REL_STRENGTH_LOOKBACK_DAYS,
    DETERIORATION_PEAK_FALLBACK_BARS,
    MATERIAL_ADD_RESET_THRESHOLD,
    POSITION_SETTLING_DAYS,
    RISK_OFF_TREND_MA,
    RISK_OFF_VIX_LEVEL,
    RISK_OFF_NAME_MIN_BETA,
    RISK_OFF_TRIM_TOP_N,
    RISK_OFF_TRIM_PCT,
    STOP_TIGHTEN_ATR_MULT,
)

WATCH = "WATCH"
TRIM = "TRIM"
EXIT = "EXIT"

# Tier rank for sorting / escalation comparisons (higher = stronger).
TIER_RANK = {WATCH: 1, TRIM: 2, EXIT: 3}


def material_add_window_days(lots) -> int | None:
    """Days since the most recent MATERIAL add, or None.

    A "material add" is a NON-initial lot whose shares are ≥
    MATERIAL_ADD_RESET_THRESHOLD % of the current open position. When the user
    averages down (or pyramids) materially, the relevant high-water mark resets
    to that decision point — otherwise the deterioration peak window spans back
    to the original entry and a stale pre-add high fabricates a large
    drawdown-from-peak (a false EXIT). The caller passes the return value as
    `assess_holding(peak_window_days=...)` to clip the peak window to "since the
    add".

    `lots`: the open tax lots oldest→newest, each `{"shares", "days_held", ...}`
    (the shape returned by tax_advisor._build_open_lots). Returns None for no
    lots, a single lot (just the original entry — nothing to re-anchor to), or
    no qualifying add. Any second-or-later purchase that clears the threshold
    counts (a position built over two same-week buys re-anchors to the second —
    harmless, it only shortens the window slightly).

    Safe-by-construction: the share fraction is taken against the CURRENT
    post-SELL open total (FIFO has already consumed earlier lots), so a later
    lot's fraction can only be inflated, never understated — i.e. re-anchoring
    can only SHORTEN the peak window, never lengthen it. A shorter window means a
    smaller measured drawdown, so this can only ever SUPPRESS a (false) exit,
    never manufacture one. Don't "fix" the denominator to the gross bought total.
    """
    if not lots or len(lots) < 2:
        return None
    total = sum((l.get("shares") or 0) for l in lots)
    if total <= 0:
        return None
    # Skip the oldest lot (the original entry); a re-anchoring add must come
    # AFTER it. Among qualifying adds, the most recent (smallest days_held) wins.
    newest_material = None
    for l in lots[1:]:
        sh = l.get("shares") or 0
        if (sh / total) * 100.0 >= MATERIAL_ADD_RESET_THRESHOLD:
            d = l.get("days_held")
            if d is not None:
                newest_material = d if newest_material is None else min(newest_material, d)
    return newest_material


def _trim_floor(atr_pct: float) -> float:
    """ATR-scaled TRIM drawdown floor, capped by the ceiling."""
    return min(max(DETERIORATION_TRIM_DD_PCT, DETERIORATION_ATR_MULT_TRIM * atr_pct),
               DETERIORATION_TRIM_DD_CEILING)


def _exit_floor(atr_pct: float) -> float:
    """ATR-scaled EXIT drawdown floor, capped by the ceiling."""
    return min(max(DETERIORATION_EXIT_DD_PCT, DETERIORATION_ATR_MULT_EXIT * atr_pct),
               DETERIORATION_EXIT_DD_CEILING)


def classify_deterioration_tier(
    *,
    dd_from_peak_pct: float,   # POSITIVE % below the high-water mark (8.0 = 8% below)
    atr_pct: float,            # ATR as % of price
    trend_broken_now: bool,    # close < trend MA on the latest bar
    below_ma_count: int,       # sessions below the MA in the last CONFIRM_DAYS
    rel_strength: float,       # name return − benchmark return over the lookback (pct pts); <0 = weaker
    price: float,
    avg_cost: float,           # cost basis per share
    dollar_pnl: float,         # unrealized $ (negative = loss)
    age_days: int | None,
) -> str | None:
    """Return the deterioration tier (EXIT | TRIM | WATCH) or None.

    Pure scalar decision core — no pandas, no I/O. Strongest tier first; a deep
    drawdown reaches EXIT without the multi-session trend confirmation (depth is
    its own confirmation), and a deep EXIT is never silenced by settling grace.
    """
    if dd_from_peak_pct is None or price is None or price <= 0:
        return None

    trim_floor = _trim_floor(atr_pct)
    exit_floor = _exit_floor(atr_pct)
    in_settling = age_days is not None and age_days < POSITION_SETTLING_DAYS

    # TRIM base conditions: past the ATR-scaled floor, trend confirmed (2 of 3
    # below the MA), AND idiosyncratically weak (not just a market-wide down day).
    trim_active = (
        dd_from_peak_pct >= trim_floor
        and below_ma_count >= DETERIORATION_CONFIRM_REQUIRED
        and rel_strength < 0
    )

    # EXIT escalation off an active TRIM: underwater, large $ loss, or deep dd.
    escalate = (
        price < avg_cost
        or dollar_pnl <= -abs(DETERIORATION_EXIT_DOLLAR_LOSS)
        or dd_from_peak_pct >= exit_floor
    )

    # Deep-drawdown EXIT shortcut: fires WITHOUT the 2-of-3 confirmation so a
    # one-session gap-down past the deep floor doesn't lag. Still requires the
    # current bar to be below the trend MA (don't exit a name at/above its MA).
    deep_exit = dd_from_peak_pct >= exit_floor and trend_broken_now

    if deep_exit or (trim_active and escalate):
        return EXIT   # danger — never silenced by settling grace

    if trim_active:
        return None if in_settling else TRIM

    # WATCH (awareness only): RS-independent — a name down ≥ WATCH_DD% and below
    # its trend MA is worth watching regardless of the market.
    if dd_from_peak_pct >= DETERIORATION_WATCH_DD_PCT and trend_broken_now:
        return None if in_settling else WATCH

    return None


def _series_close(df):
    """Return the clean Close series (NaN-Close bars dropped), or None."""
    if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    return s if not s.empty else None


def _pct_return(close, lookback: int):
    """Simple return over `lookback` bars, or None if not enough history."""
    if close is None or len(close) <= lookback:
        return None
    prev = float(close.iloc[-(lookback + 1)])
    if prev <= 0:
        return None
    return float(close.iloc[-1]) / prev - 1.0


def assess_holding(
    ticker: str,
    df,
    spy_df=None,
    *,
    price: float | None = None,
    atr: float | None = None,
    avg_cost: float | None = None,
    shares: float | None = None,
    pnl_pct: float | None = None,
    weight_pct: float | None = None,
    age_days: int | None = None,
    peak_window_days: int | None = None,
) -> dict | None:
    """Extract the deterioration scalars from price history and classify.

    df            : the indicator frame (has Close + SMA_<DETERIORATION_TREND_MA>).
    spy_df        : benchmark history for relative strength (optional → RS treated
                    as 0, which keeps the action tiers from firing on unknown RS).
    peak_window_days : override for the high-water-mark window (caller passes a
                    material-add re-anchored window); defaults to age_days.

    Returns a payload dict (tier + the figures the brief needs to render the
    directive / why / trigger and to sort by dollar risk), or None when there is
    no signal or insufficient data.
    """
    close = _series_close(df)
    if close is None or price is None or price <= 0:
        return None

    ma_col = f"SMA_{DETERIORATION_TREND_MA}"
    if ma_col not in df.columns:
        return None
    ma = df[ma_col]
    sma_now = ma.iloc[-1]
    # Need a valid trend MA to assess a trend break; a NaN MA (insufficient
    # history) means no signal rather than a fabricated one.
    if sma_now is None or sma_now != sma_now:   # NaN check without importing math
        return None
    trend_broken_now = float(price) < float(sma_now)

    # Sessions below the MA over the confirmation window.
    k = DETERIORATION_CONFIRM_DAYS
    tail_close = close.tail(k)
    tail_ma = ma.tail(k)
    below_ma_count = 0
    for c, m in zip(tail_close.tolist(), tail_ma.tolist()):
        if m == m and c == c and c < m:   # skip NaN MA/close bars
            below_ma_count += 1

    # High-water mark over the holding window (re-anchored window if provided).
    window = peak_window_days if peak_window_days is not None else age_days
    if window is not None and window > 0:
        # Calendar days → approx trading bars; +2 cushion, floor of 2.
        n = max(2, int(round(window * 5.0 / 7.0)) + 2)
        peak_series = close.tail(n)
    else:
        # No journal age — bound the lookback so an old pre-entry high can't
        # fabricate a huge drawdown. ~3 trading months.
        peak_series = close.tail(DETERIORATION_PEAK_FALLBACK_BARS)
    peak = float(peak_series.max())
    if peak <= 0:
        return None
    dd_from_peak_pct = max(0.0, (peak - float(price)) / peak * 100.0)

    atr_pct = (float(atr) / float(price) * 100.0) if atr else 0.0

    # Relative strength vs benchmark over the lookback (pct points). Unknown → 0
    # (not negative), so the action tiers never fire on missing RS.
    name_ret = _pct_return(close, REL_STRENGTH_LOOKBACK_DAYS)
    spy_ret = _pct_return(_series_close(spy_df), REL_STRENGTH_LOOKBACK_DAYS)
    rel_strength = ((name_ret - spy_ret) * 100.0) if (name_ret is not None and spy_ret is not None) else 0.0

    avg_cost = float(avg_cost) if avg_cost is not None else float(price)
    shares = float(shares) if shares is not None else 0.0
    dollar_pnl = (float(price) - avg_cost) * shares

    tier = classify_deterioration_tier(
        dd_from_peak_pct=dd_from_peak_pct,
        atr_pct=atr_pct,
        trend_broken_now=trend_broken_now,
        below_ma_count=below_ma_count,
        rel_strength=rel_strength,
        price=float(price),
        avg_cost=avg_cost,
        dollar_pnl=dollar_pnl,
        age_days=age_days,
    )
    if tier is None:
        return None

    return {
        "ticker": ticker,
        "tier": tier,
        "dd_from_peak_pct": round(dd_from_peak_pct, 1),
        "peak": round(peak, 2),
        "price": round(float(price), 2),
        "atr_pct": round(atr_pct, 1),
        "rel_strength": round(rel_strength, 1),
        "below_ma_count": below_ma_count,
        "trend_ma": DETERIORATION_TREND_MA,
        "sma": round(float(sma_now), 2),
        "trim_floor": round(_trim_floor(atr_pct), 1),
        "exit_floor": round(_exit_floor(atr_pct), 1),
        "dollar_pnl": round(dollar_pnl, 0),
        "pnl_pct": pnl_pct,
        "weight_pct": weight_pct,
        "shares": int(shares),
        "dollar_risk": round(float(price) * shares, 0),   # position size — Act-Today sort tiebreak
    }


# ── Risk-off protective de-risk (Phase 2) ─────────────────────────────────────
# Phase 1 (above) handles IDIOSYNCRATIC deterioration and deliberately SKIPS
# market-wide down days (the relative-strength gate). This layer closes that gap:
# when the whole market is in a risk-off REGIME and THIS book is fragile, promote
# the standing Fragility awareness into a concrete trim on the names actually
# driving the risk. Regime (not a single down day) so we don't sell the dip.

def risk_off_regime(spy_trend_df, vix_level, *, trend_ma, vix_threshold):
    """Is the market in a risk-off regime? Returns (bool, reasons: list[str]).

    Two independent legs — EITHER trips it (a trend break or a vol spike is each
    sufficient grounds to de-risk):
      • Trend: SPY's latest close below its `trend_ma`-day SMA (Faber).
      • Vol:   VIX ≥ `vix_threshold` (high-vol regime).
    Each leg degrades to "not tripped" on missing/short data — never fabricates a
    risk-off read from absent inputs.
    """
    reasons: list[str] = []
    close = _series_close(spy_trend_df)
    if close is not None and len(close) >= trend_ma:
        sma = float(close.tail(trend_ma).mean())
        last = float(close.iloc[-1])
        if sma > 0 and last < sma:
            reasons.append(f"S&P 500 is below its {trend_ma}-day average (downtrend)")
    try:
        if vix_level is not None and float(vix_level) >= float(vix_threshold):
            reasons.append(f"VIX {float(vix_level):.0f} ≥ {float(vix_threshold):.0f} (elevated volatility)")
    except (TypeError, ValueError):
        pass
    return (len(reasons) > 0, reasons)


def assess_risk_off_derisk(
    port_df,
    held_data,
    *,
    fragility,
    spy_trend_df,
    vix_level,
    exclude_tickers=None,
    trend_ma: int = RISK_OFF_TREND_MA,
    vix_threshold: float = RISK_OFF_VIX_LEVEL,
    name_min_beta: float = RISK_OFF_NAME_MIN_BETA,
    top_n: int = RISK_OFF_TRIM_TOP_N,
    trim_pct: float = RISK_OFF_TRIM_PCT,
    stop_tighten_atr_mult: float = STOP_TIGHTEN_ATR_MULT,
) -> list[dict]:
    """Risk-off de-risk cards for the top beta contributors — or [] if not armed.

    Two AND-gates (a LIGHT overlay, not a market-timer):
      1. The book is fragile — `fragility["severity"]` in {caution, fragile}
         (already encodes elevated portfolio beta, so no separate beta knob).
      2. The market is in a risk-off regime (`risk_off_regime`).
    Then rank holdings by beta-contribution (β × weight), keep those with
    β ≥ `name_min_beta`, take the top `top_n`, and EXCLUDE any ticker already
    carrying a higher-priority reduce (passed in `exclude_tickers`) so a name is
    never double-surfaced. Each card suggests a modest trim OR a stop-tighten
    ("don't sell into weakness" option) — directives only, the user decides.
    """
    if port_df is None or getattr(port_df, "empty", True):
        return []
    sev = (fragility or {}).get("severity")
    if sev not in ("caution", "fragile"):
        return []
    armed, reasons = risk_off_regime(
        spy_trend_df, vix_level, trend_ma=trend_ma, vix_threshold=vix_threshold
    )
    if not armed:
        return []

    exclude = {str(t).upper() for t in (exclude_tickers or [])}
    contribs: list[dict] = []
    for _, row in port_df.iterrows():
        t = str(row.get("Ticker", "")).upper()
        if not t or t in exclude:
            continue
        rm = ((held_data or {}).get(t) or {}).get("risk_metrics") or {}
        beta = rm.get("beta")
        try:
            beta = float(beta) if beta is not None else None
        except (TypeError, ValueError):
            beta = None
        try:
            w = float(row.get("Weight (%)"))
        except (TypeError, ValueError):
            w = 0.0
        if beta is None or beta < name_min_beta or w <= 0:
            continue
        try:
            price = float(row.get("Price"))
        except (TypeError, ValueError):
            price = 0.0
        try:
            shares = float(row.get("Shares"))
        except (TypeError, ValueError):
            shares = 0.0
        try:
            pnl = float(row.get("P&L (%)"))
        except (TypeError, ValueError):
            pnl = None
        atr = ((held_data or {}).get(t) or {}).get("atr")
        contribs.append({
            "ticker": t, "beta": beta, "weight": w, "price": price,
            "shares": shares, "pnl_pct": pnl, "atr": atr,
            "contrib": beta * w / 100.0,
        })

    if not contribs:
        return []
    contribs.sort(key=lambda x: -x["contrib"])

    reason_txt = " and ".join(reasons)
    implied = (fragility or {}).get("implied_move")
    pullback = (fragility or {}).get("pullback_pct")
    book_line = (
        f" A {abs(pullback):.0f}% market pullback implies ~{abs(implied):.0f}% on this book."
        if implied is not None and pullback else ""
    )

    cards: list[dict] = []
    for c in contribs[:top_n]:
        t = c["ticker"]
        trim_shares = int(c["shares"] * trim_pct / 100.0) if c["shares"] else 0
        # Stop-tighten alternative: ATR-based level under the current price.
        tighten_txt = ""
        if c["atr"] and c["price"] > 0:
            new_stop = c["price"] - stop_tighten_atr_mult * float(c["atr"])
            if new_stop > 0:
                tighten_txt = f" — or, if you'd rather not sell into weakness, tighten the stop to ~${new_stop:.2f}"
        trim_clause = (
            f"Trim ~{trim_pct:.0f}% ({trim_shares} share{'s' if trim_shares != 1 else ''}) of {t}"
            if trim_shares > 0 else f"Trim ~{trim_pct:.0f}% of {t}"
        )
        cards.append({
            "priority": "high",
            "icon": "🛡️",
            "ticker": t,
            "kind": "risk_off_derisk",
            "action": "TRIM — Risk-Off",
            "directive": f"{trim_clause}{tighten_txt}.",
            "why": (
                f"Your book is {sev} and the market is risk-off ({reason_txt}). "
                f"{t} is a top risk driver (β {c['beta']:.2f} · {c['weight']:.1f}% of book)."
                f"{book_line}"
            ),
            "trigger": (
                "If the market keeps deepening below trend → reduce further; "
                "if it stabilises back above trend → hold the rest."
            ),
            "weight": round(c["weight"], 1),
            "pnl_pct": c["pnl_pct"],
            "dollar_risk": round(c["price"] * c["shares"], 0),
        })
    return cards
