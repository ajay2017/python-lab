"""Pure view-helpers for the Summary page. No Streamlit, no DB."""
from __future__ import annotations

from stock_analyzer import margin as _margin
# The EXIT/TRIM severity split is OWNED by decision_bucket (derived there from
# _REDUCE_ACT_KINDS). Imported rather than re-declared so a badge here can never
# disagree with the bucket chips rendered from the same items.
from stock_analyzer.decision_bucket import EXIT_KINDS as _EXIT_KINDS
from stock_analyzer.decision_bucket import TRIM_KINDS as _TRIM_KINDS


def book_safety(
    leverage: dict | None,
    broker_drift: dict | None,
    *,
    maintenance_rate: float,
    fragility_pullback_pct: float,
) -> dict:
    """Classify book safety for the Safety strip.

    Parameters
    ----------
    leverage       : _leverage_cache from session_state, or None if not loaded.
    broker_drift   : _broker_drift_cache from session_state, or None.
    maintenance_rate : Reg-T maintenance rate as a decimal (e.g. 0.25).
                       Pass constants.MARGIN_MAINTENANCE_RATE.
    fragility_pullback_pct : Routine-correction yardstick (e.g. -10.0).
                             Pass constants.FRAGILITY_PULLBACK_PCT.

    Returns
    -------
    dict with keys:
      level            : "green" | "amber" | "red" | "unknown"
      headline         : str  — the words for this verdict, ALWAYS present.
                         Part of the contract, not an optional adornment: the
                         renderer reads it unconditionally, and it lives here
                         rather than in a level→word map because `red` has three
                         distinct causes and one word cannot serve all of them.
      leverage_x       : float | None   — gross leverage ratio
      cushion          : float | None   — dollar cushion above maintenance
      maintenance_req  : float | None   — current maintenance requirement
      call_distance_pct: float | None   — % decline before a margin call fires
                                          (negative = distance remaining)
      in_call          : bool | None
      drift_state      : "in_sync" | "drift" | "not_checked"
      reasons          : list[str]       — short human-readable reasons for red

    Design rules. The governing principle is that this function feeds a
    COLOUR, and a colour is an affirmative claim — so every path that cannot
    substantiate a claim returns "unknown" rather than the reassuring answer.

      - leverage is None                → "unknown". NEVER green.
      - leverage["stale"] is True       → "unknown" (an old debit figure may be
        describing a book that has since levered up; both green and red would be
        confident wrong answers).
      - margin_debit <= 0 AND cash_seen → "green". The `cash_seen` conjunct is
        load-bearing: margin_debit == 0 is ALSO what a missing account_cash row
        and a thrown DB read look like, because the publisher fails soft to its
        default (memory project_leverage_cache_false_green). Without it → unknown.
      - RED if in_call, OR the book is within fragility_pullback_pct of a call,
        OR the broker reports a live position drift. The drift leg is
        INDEPENDENT of financing and is evaluated on the unlevered path too.
      - AMBER: levered, cushion beyond the fragility yardstick, no drift.
      - broker_drift state "unknown"/"none"/None → drift_state="not_checked".
        Never forces red, and never reads as in_sync.
    """
    # `headline` travels WITH the level, rather than being re-derived from a
    # level→word map in the renderer. `red` has more than one cause — a margin
    # call, a near-call cushion, or a broker drift on an otherwise unlevered
    # book — so a single word keyed on the level alone would print "Margin risk"
    # above "Leverage 1.00×". Review finding 2026-08-28, introduced by hoisting
    # the drift leg; keeping the wording next to the classification that
    # produced it is the fix that cannot drift again.
    base: dict = {
        "level": "unknown",
        "headline": "Leverage not verified this session",
        "leverage_x": None,
        "cushion": None,
        "maintenance_req": None,
        "call_distance_pct": None,
        "in_call": None,
        "drift_state": "not_checked",
        "reasons": [],
    }

    # ── Broker drift state ────────────────────────────────────────────────────
    # TRAP, do not "complete" this mapping. broker_sync.decide_drift_banner
    # emits none / unknown / stale_clean / drift. `"in_sync"` is NOT in that
    # vocabulary — it is accepted here only so a future producer that adds an
    # explicit positive state maps correctly. In particular `"none"` must stay
    # on the not_checked branch: it means "no broker comparison was made" (no
    # broker configured, nothing to compare), NOT "the app and broker agree".
    # Mapping "none" → in_sync would launder an absent check into a clean bill
    # of health, which is this module's whole reason for existing.
    if broker_drift is not None:
        raw_state = str(broker_drift.get("state") or "unknown")
        if raw_state == "in_sync":
            base["drift_state"] = "in_sync"
        elif raw_state == "drift":
            base["drift_state"] = "drift"
        else:
            base["drift_state"] = "not_checked"

    # ── Leverage unknown / not loaded ─────────────────────────────────────────
    if leverage is None:
        return base  # level stays "unknown"

    # ── Stale debit data → unknown (same posture as unloaded) ─────────────────
    if leverage.get("stale"):
        base["headline"] = "Leverage figure is out of date"
        base["reasons"] = ["Margin debit data is stale — revisit 🏠 Home to refresh"]
        return base  # level stays "unknown"

    # ── Leverage ratio (for display) ──────────────────────────────────────────
    if leverage.get("ratio") is not None:
        try:
            base["leverage_x"] = float(leverage["ratio"])
        except (TypeError, ValueError):
            pass

    margin_debit = leverage.get("margin_debit") or 0
    try:
        margin_debit = float(margin_debit)
    except (TypeError, ValueError):
        margin_debit = 0.0

    # Broker drift is an INDEPENDENT red leg — it says the app's share counts
    # disagree with the broker, which is true regardless of how the book is
    # financed. Evaluated HERE, before the unlevered early-return, so the same
    # drift fact cannot render red on a levered book and green on an unlevered
    # one. (Blocking review finding 2026-08-28: it was previously only reached
    # on the levered path, so an unlevered book with live drift showed a green
    # strip containing a "⚠ Drift" tile — the colour contradicting its own cell.)
    _drift_red = base["drift_state"] == "drift"

    # ── Unlevered book → green, but ONLY on a measured cash balance ───────────
    # `margin_debit == 0` is ALSO what a missing account_cash row and a thrown
    # DB read look like (both fail-soft to the publish-site default), so green
    # here without `cash_seen` would be an affirmative safety claim on zero
    # evidence — shown to an owner who runs leverage by deliberate policy.
    # A cache written before `cash_seen` existed returns None → falsy → unknown,
    # which is the safe direction.
    if margin_debit <= 0:
        if not leverage.get("cash_seen"):
            base["reasons"] = [
                "Cash balance not on file — leverage cannot be verified this session"
            ]
            return base  # level stays "unknown" — never green on no evidence
        if _drift_red:
            base["level"] = "red"
            # NOT "Margin risk" — this book has no margin loan. The red is
            # entirely about the broker disagreeing with our share counts.
            base["headline"] = "Broker position drift"
            base["reasons"] = ["Broker reports position drift"]
            return base
        base["level"] = "green"
        base["headline"] = "No margin debt"
        return base

    # ── Levered — compute call distance ───────────────────────────────────────
    stock_value = leverage.get("equity")
    owner_equity = leverage.get("net_capital")
    if stock_value is None or owner_equity is None:
        return base  # level stays "unknown"

    try:
        stock_value = float(stock_value)
        owner_equity = float(owner_equity)
    except (TypeError, ValueError):
        return base

    cd = _margin.call_distance(
        stock_value=stock_value,
        owner_equity=owner_equity,
        margin_debit=margin_debit,
        rate=maintenance_rate,
    )

    if cd is None:
        # call_distance returns None when degenerate inputs — treat as unknown
        return base

    base["cushion"] = cd["cushion"]
    base["maintenance_req"] = cd["maintenance_req"]
    base["call_distance_pct"] = cd["call_distance_pct"]
    base["in_call"] = cd["in_call"]

    # ── Classify ──────────────────────────────────────────────────────────────
    reasons: list[str] = []
    is_red = False

    if cd["in_call"]:
        is_red = True
        reasons.append("In margin call")
    elif abs(cd["call_distance_pct"]) <= abs(fragility_pullback_pct):
        is_red = True
        reasons.append(
            f"Less than {abs(fragility_pullback_pct):.0f}% from a margin call"
        )

    if _drift_red:
        is_red = True
        reasons.append("Broker reports position drift")

    base["reasons"] = reasons
    base["level"] = "red" if is_red else "amber"
    # Name the DOMINANT cause, worst first, so the headline and the cells agree.
    if cd["in_call"]:
        base["headline"] = "In a margin call"
    elif is_red and abs(cd["call_distance_pct"]) <= abs(fragility_pullback_pct):
        base["headline"] = "A routine pullback would trigger a margin call"
    elif is_red:
        base["headline"] = "Broker position drift"
    else:
        base["headline"] = "Levered — cushion beyond a routine pullback"
    return base


def quote_change_pct(quote) -> float | None:
    """Today's % change for one `_live_prices` entry, or None if not knowable.

    PREFERS the provider's own `change_pct` and only falls back to recomputing
    from price/prev_close. That order matters: Finnhub (`dp`) and FMP
    (`changesPercentage`) can supply `change_pct` while leaving `prev_close`
    None, so a recompute-only reader would count those tickers "missing" while
    the Summary holdings table — which reads `change_pct` directly — showed real
    day moves for them. One screen, two contradicting claims. yfinance nulls
    both together, so it is unaffected either way.

    Returns None (never 0.0) when the change is unknown: data.fetch_live_prices'
    contract is that a missing prior close is None and is "never fabricated as
    0", and a 0.00% printed next to a real move is the confident-wrong-answer
    failure this whole module is written to avoid.
    """
    if not isinstance(quote, dict):
        return None
    raw = quote.get("change_pct")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    price, prev_close = quote.get("price"), quote.get("prev_close")
    if price is None or prev_close is None:
        return None
    try:
        price, prev_close = float(price), float(prev_close)
    except (TypeError, ValueError):
        return None
    if prev_close <= 0:
        return None
    return (price - prev_close) / prev_close * 100.0


def top_movers(
    port_df,
    live_prices: dict | None,
    *,
    n: int = 3,
) -> dict:
    """Top N up and top N down movers by today's change_pct.

    A ticker with no quote (absent from live_prices or missing prev_close) is
    excluded from sorting and counted in n_missing — NEVER rendered as 0%.

    Parameters
    ----------
    port_df     : portfolio DataFrame with at least a "Ticker" column.
    live_prices : {ticker: {"price": float, "prev_close": float, ...}} or None.
    n           : how many movers per direction to return.

    Returns
    -------
    dict with keys:
      up        : list of {"ticker": str, "change_pct": float}  (descending)
      down      : list of {"ticker": str, "change_pct": float}  (ascending)
      combined  : the n biggest moves either way, ranked by |change_pct| --
                  a FIXED row count, which is what the KPI tile renders
      n_priced / n_missing
      n_priced  : int
      n_missing : int
    """
    if port_df is None or (hasattr(port_df, "empty") and port_df.empty):
        return {"up": [], "down": [], "n_priced": 0, "n_missing": 0}

    priced: list[dict] = []
    n_missing = 0

    prices = live_prices if live_prices is not None else {}

    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "")).strip().upper()
        if not ticker:
            continue
        change_pct = quote_change_pct(prices.get(ticker))
        if change_pct is None:
            n_missing += 1
            continue
        priced.append({"ticker": ticker, "change_pct": change_pct})

    # Strict > 0 / < 0: a genuine 0.00% is a MOVER OF NEITHER DIRECTION. With
    # `>= 0` it appeared under "↑" in this tile while day_direction_counts
    # counted it "flat" in the footer directly below — and routing both through
    # quote_change_pct made that disagreement deterministic rather than
    # incidental, since a provider-rounded 0.0 now reaches both the same way.
    priced_sorted = sorted(priced, key=lambda x: x["change_pct"], reverse=True)
    up_movers   = [x for x in priced_sorted if x["change_pct"] > 0][:n]
    down_movers = list(reversed([x for x in priced_sorted if x["change_pct"] < 0]))[:n]

    # `combined` = the n biggest moves in EITHER direction, ranked by magnitude.
    # The KPI strip renders this rather than up+down, because a fixed row count
    # is what keeps the tile the same height as the four st.metric tiles beside
    # it — an up/down split yields 0..2n rows and visibly broke the row's
    # alignment on the first live render. Direction is carried by the sign, so
    # nothing is lost except the guarantee that both directions appear; on a
    # uniformly green day three up-movers IS the honest answer.
    # Flat names are EXCLUDED, same rule as up/down above. A 0.00% is not a
    # mover, and the renderer derives its arrow from the sign — so a flat name
    # left in here would render "↓ TICK +0.0%", reintroducing the fabricated
    # direction the strict > 0 / < 0 split exists to prevent. Caught by
    # test_combined_excludes_flat_and_unpriced_names.
    combined = sorted(
        (x for x in priced if x["change_pct"] != 0),
        key=lambda x: abs(x["change_pct"]), reverse=True,
    )[:n]

    return {
        "up":       up_movers,
        "down":     down_movers,
        "combined": combined,
        "n_priced": len(priced),
        "n_missing": n_missing,
    }


def position_status_badge(
    *,
    reduce_call: dict | None,
    weight_pct: float,
    single_name_ceiling: float,
) -> dict | None:
    """Badge for a position row.

    Reduce call outranks CAP — if the Brief is already telling you to EXIT/TRIM
    this name, the concentration badge adds no useful information on top.

    Parameters
    ----------
    reduce_call         : the item dict from _reduce_calls[ticker], or None.
                          Expected keys: _source ("act"|"review"), kind (act),
                          action.type (review).
    weight_pct          : current gross-book weight percentage (0–100).
    single_name_ceiling : SINGLE_NAME_CEILING constant (passed from caller;
                          comparison lives HERE, not in app.py).

    Returns
    -------
    {"label": "EXIT"|"TRIM"|"WATCH"|"CAP⚠", "kind": str} or None.
    """
    if reduce_call is not None:
        label = _badge_label_from_reduce_call(reduce_call)
        return {"label": label, "kind": str(reduce_call.get("kind") or "")}

    # >= boundary: at-ceiling is a breach, matching the sizing cap convention
    if weight_pct >= single_name_ceiling:
        return {"label": "CAP⚠", "kind": "concentration"}

    return None


def _badge_label_from_reduce_call(item: dict) -> str:
    """Map a reduce-call item to an EXIT / TRIM / WATCH display label."""
    src = item.get("_source")
    if src == "act":
        kind = str(item.get("kind", ""))
        if kind in _EXIT_KINDS:
            return "EXIT"
        if kind in _TRIM_KINDS:
            return "TRIM"
        return "WATCH"
    if src == "review":
        # All review-origin reduce types are trim variants
        return "TRIM"
    return "WATCH"


def day_direction_counts(port_df, live_prices: dict | None) -> dict:
    """Count up/down/flat/missing positions for the day-direction footer.

    A ticker absent from live_prices, or whose prev_close is missing/zero,
    is counted "missing" — NEVER as flat (0%).

    Returns
    -------
    dict with keys: up, down, flat, missing  (all int)
    """
    counts = {"up": 0, "down": 0, "flat": 0, "missing": 0}
    if port_df is None or (hasattr(port_df, "empty") and port_df.empty):
        return counts

    prices = live_prices if live_prices is not None else {}

    for _, row in port_df.iterrows():
        ticker = str(row.get("Ticker", "")).strip().upper()
        if not ticker:
            continue
        # Same `_quote_change_pct` the movers tile uses — if these two read the
        # quote differently, the footer count and the tile can contradict each
        # other on one screen.
        change_pct = quote_change_pct(prices.get(ticker))
        if change_pct is None:
            counts["missing"] += 1
        elif change_pct > 0:
            counts["up"] += 1
        elif change_pct < 0:
            counts["down"] += 1
        else:
            counts["flat"] += 1

    return counts


# NOTE — there is deliberately NO `protective_signal_count(port_df)` here.
# A first version of this module counted EXIT/TRIM/WATCH out of port_df's
# "Signal" column as a Risk-Posture fallback. That column is produced by
# portfolio.build_portfolio_df as f"{rec['icon']} {rec['label']}", whose only
# possible labels are Strong Buy / Buy / Hold / Sell / Strong Sell (scoring.py)
# — it is a COMPOSITE-BAND label, not a protective-signal vocabulary. The
# matcher could therefore only ever return zero, and the caller rendered that
# zero as a green "no protective signals" all-clear, on the same screen as a red
# "Act Today (3)" strip. Caught by Opus review 2026-08-28; the unit test had
# hidden it by inventing the input instead of reading the producer
# (memory feedback_validation_reads_detector_source).
#
# The Risk Posture fallback now reads the ALREADY-DECIDED protective items via
# decision_bucket.bucket_act_by_type(), which is the canonical classifier, and
# renders "not computed" — never green — when that source is absent.


def avg_score_label(avg_score: float, composite_buy: float) -> str:
    """One-line label for the Avg Score KPI tile.

    Parameters
    ----------
    avg_score     : mean composite score across held positions.
    composite_buy : COMPOSITE_BUY constant (comparison lives here, not in app.py).

    Returns
    -------
    "above buy threshold" or "below buy threshold"
    """
    if avg_score >= composite_buy:
        return "above buy threshold"
    return "below buy threshold"
