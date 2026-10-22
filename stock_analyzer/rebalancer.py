"""
Portfolio Rebalancing Advisor.

Computes drift between current and target weights, ranks positions by
rebalancing urgency, and generates specific trim/add actions with dollar
and share amounts, priority ordering, and Institutional-style context on
when and how to rebalance.
"""

import pandas as pd

from stock_analyzer.constants import (
    COMPOSITE_HOLD, COMPOSITE_BUY, TOLERANCE_OK, TOLERANCE_WATCH,
)


def _f(val, default=0.0):
    if val is None:
        return default
    try:
        f = float(val)
        return default if (f != f) else f
    except (TypeError, ValueError):
        return default


# Tolerance bands (TOLERANCE_OK / TOLERANCE_WATCH) live in constants.py.


def equal_weights(port_df: pd.DataFrame) -> dict:
    """Return equal target weight for each ticker."""
    n = len(port_df)
    if n == 0:
        return {}
    w = round(100.0 / n, 2)
    return {row["Ticker"]: w for _, row in port_df.iterrows()}


def compute_drift(
    port_df: pd.DataFrame,
    target_weights: dict,
    total_val: float,
) -> pd.DataFrame:
    """
    Returns DataFrame with per-position drift analysis.
    Columns: Ticker, Sector, Current (%), Target (%), Drift (pp),
             Current Value ($), Target Value ($), Drift Value ($),
             Price ($), Shares, Score, Signal, Status
    """
    rows = []
    for _, row in port_df.iterrows():
        ticker  = row["Ticker"]
        current = _f(row.get("Weight (%)"))
        target  = _f(target_weights.get(ticker, current))
        drift   = round(current - target, 2)  # positive = overweight
        mval    = _f(row.get("Market Value"))
        price   = _f(row.get("Price"))
        shares  = _f(row.get("Shares"))
        score   = _f(row.get("Score"))
        signal  = str(row.get("Signal", ""))
        sector  = str(row.get("Sector", "Other"))
        score_available = bool(row.get("Score Available", True))

        target_val = target / 100 * total_val
        drift_val  = round(mval - target_val, 0)  # positive = trim, negative = add

        if abs(drift) <= TOLERANCE_OK:
            status = "OK"
        elif abs(drift) <= TOLERANCE_WATCH:
            # "DRIFTING" not "WATCH" — this is a sizing-tolerance band, a
            # different concept from exit_advisor's deterioration WATCH tier
            # (2026-08-04 UX audit CA2).
            status = "DRIFTING"
        else:
            status = "TRIM" if drift > 0 else "ADD"

        rows.append({
            "Ticker":           ticker,
            "Sector":           sector,
            "Current (%)":      round(current, 1),
            "Target (%)":       round(target, 1),
            "Drift (pp)":       drift,
            "Current Value ($)": round(mval, 0),
            "Target Value ($)": round(target_val, 0),
            "Drift Value ($)":  round(drift_val, 0),
            "Price ($)":        round(price, 2),
            "Shares":           int(shares),
            "Score":            score,
            "Signal":           signal,
            "Status":           status,
            "Score Available":  score_available,
        })

    return pd.DataFrame(rows).sort_values("Drift (pp)", ascending=False).reset_index(drop=True)


def drift_bar_color(drift_pp: float) -> str:
    """Bar colour for the drift chart: red = overweight past the action band,
    amber = overweight in the watch band, blue = underweight past tolerance,
    gray = in tolerance. Decision lives here, not in app.py."""
    if drift_pp > TOLERANCE_WATCH:
        return "#ff4444"
    if drift_pp > TOLERANCE_OK:
        return "#ffbb33"
    if drift_pp < -TOLERANCE_OK:
        return "#4a9eff"
    return "#888888"


def build_rebalance_plan(
    drift_df: pd.DataFrame,
    total_val: float,
    news_flags: dict | None = None,
    risk_trim_set: set | None = None,
    reduce_call_set: set | None = None,
    add_verdicts: dict | None = None,
) -> dict:
    """
    Generates trim actions with specific share counts and underweight (add)
    rows as drift only (S-D C5: no share counts; Grow Today sizes every buy),
    with urgency ordering and Institutional-style rationale.

    news_flags (optional): {ticker: {"level": "critical"|"warning",
                                     "headline": str, "compound": float}}
       — ADD actions on flagged tickers get a news_warning attached so the
       rebalancer never tells the user to buy more of a position that News
       Intelligence is currently flagging negative.

    risk_trim_set (optional): set of tickers Risk Advisor is recommending the
       user trim (beta or Sharpe drag). ADD actions on these tickers are
       suppressed entirely — drift-driven sizing must not override an active
       investment-level recommendation to reduce exposure.

    reduce_call_set (optional): set of tickers under an active Reduce/Exit call
       from today's Daily Brief (stop breach, sell signal, deterioration exit,
       or review-list trim). ADD actions on these tickers are suppressed
       entirely — checked BEFORE risk_trim_set, since a Brief-driven reduce
       call is a more specific, time-sensitive signal than the portfolio-level
       beta/Sharpe metric.

    add_verdicts (optional): {UPPERCASE ticker: verdict dict} from
       sizing_path.held_add_verdict ({"state": "add"|"paused"|"not_called"|
       "unknown", "reason", "gate_id", "item"}). S-D C5: a held name's add is
       Grow Today's call, not the Rebalancer's. A "paused" verdict moves the
       row to grow_paused_adds (checked AFTER reduce_call_set and
       risk_trim_set). Surviving add rows carry no share count: they show
       drift plus Grow Today's verdict (grow_call / grow_call_text). None means
       the cross-check couldn't run: every surviving add row reads "unknown".

    Returns dict with:
      trims, adds, ok, total_trim_value, total_add_value, rebalance_pct,
      news_blocked_adds, risk_blocked_adds_count, reduce_blocked_adds,
      reduce_blocked_adds_count, grow_paused_adds, grow_paused_adds_count,
      trim_pct (trims only; rebalance_pct still sums trim + add drift)
    """
    if drift_df.empty:
        return {"trims": [], "adds": [], "ok": [], "total_trim_value": 0,
                "total_add_value": 0, "rebalance_pct": 0,
                "news_blocked_adds": 0, "risk_blocked_adds_count": 0,
                "risk_blocked_adds": [],
                "reduce_blocked_adds": [], "reduce_blocked_adds_count": 0,
                "grow_paused_adds": [], "grow_paused_adds_count": 0,
                "trim_pct": 0.0, "trim_action_needed": False}

    news_flags      = news_flags or {}
    risk_trim_set   = {str(t).upper() for t in (risk_trim_set or set())}
    reduce_call_set = {str(t).upper() for t in (reduce_call_set or set())}

    trims, adds, ok_list = [], [], []
    risk_blocked_adds_list: list[dict] = []
    reduce_blocked_adds_list: list[dict] = []
    grow_paused_adds_list: list[dict] = []

    for _, row in drift_df.iterrows():
        ticker    = row["Ticker"]
        status    = row["Status"]
        drift_pp  = row["Drift (pp)"]
        drift_val = row["Drift Value ($)"]
        price     = row["Price ($)"]
        shares    = row["Shares"]
        score     = _f(row["Score"])
        signal    = str(row["Signal"])
        current   = row["Current (%)"]
        target    = row["Target (%)"]
        sector    = row["Sector"]
        # Fundamentals-withhold consistency (fail-open True: a legacy
        # drift_df built before compute_drift carried this column must not
        # be treated as withheld). See portfolio.build_portfolio_df — when
        # False, `score` above is _f()'s 0.0 coercion of a withheld NaN, not
        # a real measurement, so neither the urgency bump nor the
        # score-mentioning rationale below may treat it as one.
        _score_ok = bool(row.get("Score Available", True))

        if status == "OK":
            ok_list.append(ticker)
            continue

        # Floor, never round up: a sub-one-share drift must not become a
        # 1-share sell that overshoots below target. 0 = no share count.
        shares_delta = int(abs(drift_val) / price) if price > 0 else 0

        # Trim urgency scoring — score thresholds aligned with scoring.py
        # signal boundaries so the rebalancer speaks the same vocabulary as
        # the composite recommendation. Sell zone = score < COMPOSITE_HOLD.
        if drift_pp > 0:
            urgency = 0
            if "Sell" in signal or "Strong Sell" in signal:
                urgency += 40   # broken thesis + overweight = highest priority
            if _score_ok and score < COMPOSITE_HOLD:
                urgency += 20
            if abs(drift_pp) > TOLERANCE_WATCH:
                urgency += 30
            elif abs(drift_pp) > TOLERANCE_OK:
                urgency += 10
            if status == "DRIFTING":
                urgency = max(urgency, 5)

            # Rationale
            _sell_txt = (
                f"{shares_delta:,} share{'' if shares_delta == 1 else 's'} "
                f"(≈${abs(drift_val):,.0f})"
            )
            if "Sell" in signal:
                rationale = (
                    f"{ticker} is {drift_pp:+.1f}pp overweight AND has a Sell signal "
                    f"(score {score:.0f}/100). Carrying oversized exposure in a position "
                    f"your own signals are flagging bearish is a double risk."
                )
                action_detail = (
                    f"Sell {_sell_txt} to bring "
                    f"weight from {current:.1f}% → {target:.1f}%. "
                    "Priority: do this before considering any other trim."
                )
            elif not _score_ok:
                # Fundamentals unavailable (fund/ETF or a data outage) — never
                # assert "Sell zone" or a numeric score for a composite that
                # was never actually measured. Drift/concentration alone
                # drives this trim; the trim ACTION itself is unchanged.
                rationale = (
                    f"{ticker} is {drift_pp:+.1f}pp overweight (conviction unmeasured — "
                    "fundamentals unavailable). Drift/concentration alone drives this trim, "
                    "not a broken thesis."
                )
                action_detail = (
                    f"Sell {_sell_txt} to bring "
                    f"weight from {current:.1f}% → {target:.1f}%."
                )
            elif score < COMPOSITE_HOLD:
                rationale = (
                    f"{ticker} is {drift_pp:+.1f}pp overweight with a score in the Sell zone "
                    f"({score:.0f}/100, below {COMPOSITE_HOLD}). You've drifted into a large "
                    f"position in a name where conviction is broken."
                )
                action_detail = (
                    f"Sell {_sell_txt} to reduce "
                    f"from {current:.1f}% → {target:.1f}%. "
                    "Recycle proceeds into higher-conviction names."
                )
            else:
                rationale = (
                    f"{ticker} has drifted {drift_pp:+.1f}pp above target weight — "
                    f"a winner running. Score {score:.0f}/100 supports the thesis, "
                    f"but concentration risk increases as the position grows."
                )
                action_detail = (
                    f"Sell {_sell_txt} to trim "
                    f"from {current:.1f}% → {target:.1f}%. "
                    "Keep the core position — just cap the binary event risk from concentration."
                )

            if shares_delta < 1:
                # No share count: sub-one-share drift, or no usable price.
                # One `$` per string (two on a line render as LaTeX).
                if price > 0:
                    action_detail = (
                        f"The drift (≈${abs(drift_val):,.0f}) is less than one share "
                        f"at {price:,.2f}, so no trim is sized; selling one share "
                        f"would take {ticker} below target."
                    )
                else:
                    action_detail = (
                        "Price unavailable, so no share count; the drift is "
                        f"≈${abs(drift_val):,.0f}."
                    )

            inst_lens = (
                "Institutional position sizing rule: no single name above 15% of portfolio,"
                "regardless of conviction — and even high-conviction names should be "
                "trimmed back to target when they outrun it by 5pp or more. "
                "The reason is not that you're wrong; it's that a concentrated winner "
                "creates asymmetric downside if conditions change. "
                "Trim the drift, not the thesis."
            )

            trims.append({
                "ticker":        ticker,
                "sector":        sector,
                "signal":        signal,
                "score":         score,
                "current_pct":   current,
                "target_pct":    target,
                "drift_pp":      drift_pp,
                "drift_val":     abs(drift_val),
                "shares_delta":  shares_delta,
                "price":         price,
                "urgency":       urgency,
                "status":        status,
                "rationale":     rationale,
                "action_detail": action_detail,
                "institutional_lens":       inst_lens,
            })

        else:  # underweight → ADD
            # Daily Brief reduce-call gate: suppress ADD entirely if today's
            # Brief has an active Reduce/Exit call on this ticker (stop breach,
            # sell signal, deterioration exit, review-list trim). Checked BEFORE
            # the Risk Advisor gate below — a Brief-driven call is more specific
            # and time-sensitive than the portfolio-level beta/Sharpe metric, so
            # if a ticker is in both sets it should report here only (no
            # double-report — this project's "each risk surfaces once" rule).
            if str(ticker).upper() in reduce_call_set:
                reduce_blocked_adds_list.append({
                    "ticker":      ticker,
                    "current_pct": current,
                    "target_pct":  target,
                    "drift_pp":    drift_pp,
                    "score":       score,
                    "signal":      signal,
                    "reason": (
                        f"Today's Brief has an active Reduce/Exit call on {ticker}. "
                        "Rebalance ADD suppressed — resolve that call first."
                    ),
                })
                continue

            # Risk Advisor TRIM gate: suppress ADD entirely if Risk Advisor is
            # recommending the user reduce exposure to this ticker. Drift-driven
            # sizing must not override an active investment-level reduce signal.
            if str(ticker).upper() in risk_trim_set:
                risk_blocked_adds_list.append({
                    "ticker":      ticker,
                    "current_pct": current,
                    "target_pct":  target,
                    "drift_pp":    drift_pp,
                    "score":       score,
                    "signal":      signal,
                    "reason": (
                        f"Risk Advisor recommends trimming {ticker} (beta or Sharpe drag). "
                        "Rebalance ADD suppressed — resolve the investment-level trim first."
                    ),
                    # Gate Suppression Ledger (roadmap B2, 2026-09-13) — G-02
                    # capture. All three already in loop scope; no recompute.
                    "price":           price,
                    "composite_score": score,
                    "sector":          sector,
                })
                continue

            # S-D C5: Grow Today owns every buy. If it paused adds to this
            # name today (G-24/G-25/G-26, cooldown, cards ...), don't suggest
            # one here. Grow Today logs its own gate rows: no ledger capture.
            v = None
            if add_verdicts is not None:
                v = add_verdicts.get(str(ticker).strip().upper())
            if isinstance(v, dict) and v.get("state") == "paused":
                grow_paused_adds_list.append({
                    "ticker":      ticker,
                    "current_pct": current,
                    "target_pct":  target,
                    "drift_pp":    drift_pp,
                    "score":       score,
                    "signal":      signal,
                    "gate_id":     v.get("gate_id"),
                    "reason": (v.get("reason")
                               or "Grow Today paused adds to this name today."),
                })
                continue

            _v_state = v.get("state") if isinstance(v, dict) else None
            grow_call = _v_state if _v_state in ("add", "not_called") else "unknown"
            if grow_call == "add":
                grow_call_text = (
                    f"Grow Today is calling an add on {ticker} today; "
                    "its size is on 🏠 Home → Grow Today."
                )
            elif grow_call == "not_called":
                grow_call_text = "No add call today: " + (
                    v.get("reason")
                    or "Grow Today isn't calling an add on this name today.")
            else:
                grow_call_text = (
                    v.get("reason") if isinstance(v, dict) and v.get("reason")
                    else "Grow Today's add call for today isn't available — "
                         "open 🏠 Home to build it.")
            action_detail = (
                f"Underweight by ≈${abs(drift_val):,.0f} "
                f"({current:.1f}% vs a {target:.1f}% target). "
                "The Rebalancer measures drift; it doesn't size buys. "
                + grow_call_text
            )

            # Add urgency: high-conviction underweight names first
            urgency = 0
            if score >= COMPOSITE_BUY:
                urgency += 30
            if "Buy" in signal or "Strong Buy" in signal:
                urgency += 20
            if abs(drift_pp) > TOLERANCE_WATCH:
                urgency += 30
            elif abs(drift_pp) > TOLERANCE_OK:
                urgency += 10

            if score >= COMPOSITE_BUY and ("Buy" in signal):
                rationale = (
                    f"{ticker} is {abs(drift_pp):.1f}pp below target weight. "
                    f"Score {score:.0f}/100 · {signal}."
                )
            elif not _score_ok:
                # Same fundamentals-withhold guard as the TRIM branch above.
                # The urgency legs just above are already naturally dormant
                # here (score coerced to 0.0 by _f(), signal carries no "Buy"
                # substring), but this fallback rationale still asserted a
                # fabricated "Score 0/100" -- fixed here, not just the leg
                # that gates whether the add fires at all.
                rationale = (
                    f"{ticker} is {abs(drift_pp):.1f}pp underweight vs target. "
                    "Conviction unmeasured — fundamentals unavailable for this "
                    "holding, so this reflects drift only, not a scored recommendation."
                )
            else:
                rationale = (
                    f"{ticker} is {abs(drift_pp):.1f}pp below target weight. "
                    f"Score {score:.0f}/100 · {signal}."
                )

            inst_lens = (
                "Drift shows where the book has moved away from its target weights. "
                "Trims are sized here; adds are not — every buy, including an add to "
                "something you hold, goes through Grow Today's checks (budget, "
                "concentration, correlation cluster, pair, sector) so there is one "
                "sizing path."
            )

            # Cross-check against active News Intelligence flags. Adding to a
            # position while critical/warning news is live is a coordination bug
            # the rebalancer cannot see on its own.
            news_warning = None
            nf = news_flags.get(ticker) or news_flags.get(str(ticker).upper())
            if nf:
                _level    = nf.get("level", "warning")
                _headline = str(nf.get("headline", ""))[:140]
                _compound = _f(nf.get("compound"))
                if _level == "critical":
                    # Drop urgency to bottom — the drift list should not rank
                    # a position absorbing a critical negative catalyst first.
                    # The message gives no add advice: Grow Today owns adds.
                    urgency = min(urgency, 5)
                    news_warning = {
                        "level":    "critical",
                        "headline": _headline,
                        "compound": _compound,
                        "message": (
                            "⛔ Active critical news flag "
                            f"(sentiment {_compound:+.2f}): \"{_headline}\". "
                            "Any add goes through Grow Today's entry check."
                        ),
                    }
                else:
                    news_warning = {
                        "level":    "warning",
                        "headline": _headline,
                        "compound": _compound,
                        "message": (
                            "⚠ Negative news flag "
                            f"(sentiment {_compound:+.2f}): \"{_headline}\". "
                            "Grow Today weighs this before calling any add."
                        ),
                    }

            adds.append({
                "ticker":        ticker,
                "sector":        sector,
                "signal":        signal,
                "score":         score,
                "current_pct":   current,
                "target_pct":    target,
                "drift_pp":      drift_pp,
                "drift_val":     abs(drift_val),
                "price":         price,
                "urgency":       urgency,
                "status":        status,
                "rationale":     rationale,
                "action_detail": action_detail,
                "grow_call":     grow_call,
                "grow_call_text": grow_call_text,
                "institutional_lens":       inst_lens,
                "news_warning":  news_warning,
            })

    trims.sort(key=lambda x: x["urgency"], reverse=True)
    adds.sort(key=lambda x: x["urgency"], reverse=True)

    total_trim = sum(t["drift_val"] for t in trims)
    total_add  = sum(a["drift_val"] for a in adds)
    touched_val = total_trim + total_add
    rebalance_pct = round(touched_val / total_val * 100, 1) if total_val > 0 else 0.0

    return {
        "trims":                   trims,
        "adds":                    adds,
        "ok":                      ok_list,
        "total_trim_value":        round(total_trim, 0),
        "total_add_value":         round(total_add, 0),
        "rebalance_pct":           rebalance_pct,
        "news_blocked_adds":       sum(1 for a in adds if a.get("news_warning")),
        "risk_blocked_adds":       risk_blocked_adds_list,
        "risk_blocked_adds_count": len(risk_blocked_adds_list),
        "reduce_blocked_adds":       reduce_blocked_adds_list,
        "reduce_blocked_adds_count": len(reduce_blocked_adds_list),
        "grow_paused_adds":          grow_paused_adds_list,
        "grow_paused_adds_count":    len(grow_paused_adds_list),
        "trim_pct": round(total_trim / total_val * 100, 1) if total_val > 0 else 0.0,
        # True only when a trim is past the action band (status TRIM), not
        # merely DRIFTING (watch).
        "trim_action_needed": any(t["status"] == "TRIM" and t["shares_delta"] >= 1 for t in trims),
    }
