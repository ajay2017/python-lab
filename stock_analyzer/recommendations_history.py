"""
Recommendations History — retrospective on every pick Today's Brief has surfaced.

Reads the `recommendations` table (populated at brief-build time by app.py) and
cross-references it with the `trades` table to compute:
  - which recs got acted on (same-day trade with trigger_type='RECOMMENDATION')
  - realized + MTM outcome for acted recs (lifted from Trade Review's accounting)
  - "would-have-gained" outcome for missed recs (price_at_surface → current_price)
  - rollup metrics: action rate by period, by rec_type, by sector
  - composite-at-rec vs eventual outcome (the substrate for the eventual AI
    predictive layer — see project_ai_integration_strategy memory)

Pure logic — no Streamlit, no DB or API calls. Caller supplies:
  - recs_df         (pandas DataFrame from db.load_recommendations)
  - trades_df       (pandas DataFrame from db.load_trades)
  - current_prices  ({ticker: latest_price})
  - today           date — for marking still-open recs

All match windows are SAME-DAY: a rec is "acted" only when a trade with the
same ticker exists on the same calendar date in NY ET with
trigger_type='RECOMMENDATION'. Looser windows risk overstating action rate
and conflating distinct decisions.
"""

from datetime import date
from collections import defaultdict


def _f(v, default=0.0):
    if v is None:
        return default
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


def _to_date(v) -> date | None:
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _spy_return_pct(spy_close_by_date: dict | None, start_d: date | None,
                    today: date) -> float | None:
    """SPY % return from `start_d` to `today`, using the nearest trading-day
    close on-or-before each date. Returns None when the benchmark series is
    missing or doesn't cover the window. `spy_close_by_date` is {date: close}.
    The benchmark is the regime adjustment: a rec's alpha = its outcome minus
    this, so a down-market loss that still beat SPY reads as positive alpha.
    """
    if not spy_close_by_date or start_d is None:
        return None

    def _close_on_or_before(d: date):
        keys = [k for k in spy_close_by_date if k <= d]
        if not keys:
            return None
        return spy_close_by_date[max(keys)]

    p0 = _close_on_or_before(start_d)
    p1 = _close_on_or_before(today)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return (p1 - p0) / p0 * 100.0


# ── Match recs to trades ────────────────────────────────────────────────────

def match_recs_to_trades(recs_df, trades_df) -> list[dict]:
    """
    For each recommendation, find a same-day trade with the same ticker and
    trigger_type='RECOMMENDATION'. Returns a list of dicts (one per rec)
    enriched with `acted_on`, `acted_trade` (the matching trade row or None),
    and normalized fields suitable for downstream consumption.

    SAME-DAY semantics: rec_date must equal traded_at::date in NY ET. Acting
    a day later is treated as a distinct decision, not as following the
    recommendation.
    """
    if recs_df is None or len(recs_df) == 0:
        return []

    # Build a lookup: (ticker, date) → trade dict, restricted to RECOMMENDATION trigger
    trade_lookup: dict[tuple[str, date], dict] = {}
    if trades_df is not None and len(trades_df) > 0:
        for _, t in trades_df.iterrows():
            trig = str(t.get("trigger_type", "") or "").strip().upper()
            if trig != "RECOMMENDATION":
                continue
            td = _to_date(t.get("traded_at"))
            tk = str(t.get("ticker", "")).strip().upper()
            if td is None or not tk:
                continue
            key = (tk, td)
            # Same-day, same-ticker, multiple RECOMMENDATION trades: keep the
            # first one (chronologically earliest by id, since trades_df is
            # ordered by traded_at desc — reverse to get earliest first).
            trade_lookup.setdefault(key, {
                "id":            t.get("id"),
                "action":        str(t.get("action", "") or ""),
                "shares":        _f(t.get("shares")),
                "price":         _f(t.get("price")),
                "cost_basis":    _f(t.get("cost_basis")),
                "realized_pnl":  _f(t.get("realized_pnl")),
                "traded_at":     t.get("traded_at"),
            })

    matched: list[dict] = []
    for _, r in recs_df.iterrows():
        rd  = _to_date(r.get("rec_date"))
        tk  = str(r.get("ticker", "")).strip().upper()
        key = (tk, rd) if rd is not None else None
        trade = trade_lookup.get(key) if key else None
        matched.append({
            "id":               r.get("id"),
            "ticker":           tk,
            "rec_date":         rd,
            "rec_type":         str(r.get("rec_type", "") or ""),
            "surfaced_at":      r.get("surfaced_at"),
            "price_at_surface": _f(r.get("price_at_surface"), None) if r.get("price_at_surface") is not None else None,
            "composite_score":  r.get("composite_score"),
            "momentum_score":   r.get("momentum_score"),
            "sector":           str(r.get("sector", "") or ""),
            "conviction":       str(r.get("conviction", "") or ""),
            "verdict":          str(r.get("verdict", "") or ""),
            "thesis":           str(r.get("thesis", "") or ""),
            "acted_on":         trade is not None,
            "acted_trade":      trade,
        })
    return matched


# ── Outcome computation ─────────────────────────────────────────────────────

def compute_outcomes(matched: list[dict], current_prices: dict[str, float] | None,
                     today: date, spy_close_by_date: dict | None = None,
                     min_days: int = 0) -> list[dict]:
    """
    Add outcome fields to each matched rec:
      - outcome_pct      : float | None
                            • Acted BUY:  (current - trade.price) / trade.price * 100
                            • Acted SELL: realized_pnl / cost_basis * 100
                            • Missed:    (current - price_at_surface) / price_at_surface * 100
                            • None when no price reference
      - outcome_dollars  : float | None — share-aware where possible (acted) or
                            normalized to $1000 notional for missed (so cross-ticker
                            comparison isn't dominated by price level)
      - outcome_label    : 'win' | 'loss' | 'flat' | 'unknown'
      - days_since       : int — days from rec_date to today
      - spy_return_pct   : float | None — SPY % over rec_date→today (the regime
                            benchmark). None for acted SELLs (realized P&L spans an
                            unknown holding period that can't be benchmarked to a
                            single window) and when the SPY series is missing.
      - alpha_pct        : float | None — outcome_pct − spy_return_pct. This is the
                            regime-adjusted read: did the rec beat the market over
                            the same window? None whenever either input is None.
      - outcome_maturing : bool — True when the rec is younger than `min_days`
                            (calendar). Such recs are kept for display but excluded
                            from the scorecard aggregates (one session of wiggle
                            isn't an outcome). See REC_SCORE_MIN_DAYS.

    Pure read of current_prices / spy_close_by_date; no fetches. Caller is
    responsible for ensuring current_prices has every relevant ticker and that
    spy_close_by_date ({date: close}) covers the rec date range.
    """
    current_prices = current_prices or {}
    out: list[dict] = []
    for r in matched:
        rec = dict(r)   # don't mutate caller's data
        cur = current_prices.get(rec["ticker"])
        cur = float(cur) if (cur is not None and float(cur) > 0) else None

        if rec["acted_on"] and rec["acted_trade"]:
            t = rec["acted_trade"]
            t_action = t["action"].upper()
            if "SELL" in t_action:
                # Realized P&L authoritative from journal
                cost_b = t["cost_basis"] or (t["price"] * t["shares"])
                rec["outcome_pct"]     = (t["realized_pnl"] / cost_b * 100.0) if cost_b > 0 else None
                rec["outcome_dollars"] = round(t["realized_pnl"], 2)
            else:
                # BUY — mark to market on actual position
                if cur is not None and t["price"] > 0:
                    pct  = (cur - t["price"]) / t["price"] * 100.0
                    rec["outcome_pct"]     = pct
                    rec["outcome_dollars"] = round((cur - t["price"]) * t["shares"], 2)
                else:
                    rec["outcome_pct"]     = None
                    rec["outcome_dollars"] = None
        else:
            # Missed — what would-have-gained against price_at_surface
            pas = rec.get("price_at_surface")
            if pas is not None and pas > 0 and cur is not None:
                pct = (cur - pas) / pas * 100.0
                rec["outcome_pct"]     = pct
                # Normalize to $1000 notional so cross-ticker comparison is meaningful
                rec["outcome_dollars"] = round(pct / 100.0 * 1000.0, 2)
            else:
                rec["outcome_pct"]     = None
                rec["outcome_dollars"] = None

        # Label
        op = rec["outcome_pct"]
        if op is None:
            rec["outcome_label"] = "unknown"
        elif op > 0.5:
            rec["outcome_label"] = "win"
        elif op < -0.5:
            rec["outcome_label"] = "loss"
        else:
            rec["outcome_label"] = "flat"

        # Days since rec_date
        rd = rec["rec_date"]
        rec["days_since"] = (today - rd).days if rd is not None else None

        # Regime benchmark + alpha. SELL outcomes are realized P&L over an
        # unknown holding period, so a single rec_date→today SPY window can't
        # fairly benchmark them — leave alpha None for those.
        is_sell_acted = bool(
            rec["acted_on"] and rec["acted_trade"]
            and "SELL" in rec["acted_trade"]["action"].upper()
        )
        if is_sell_acted:
            rec["spy_return_pct"] = None
            rec["alpha_pct"]      = None
        else:
            spy_ret = _spy_return_pct(spy_close_by_date, rd, today)
            rec["spy_return_pct"] = round(spy_ret, 2) if spy_ret is not None else None
            rec["alpha_pct"] = (
                round(rec["outcome_pct"] - spy_ret, 2)
                if (rec["outcome_pct"] is not None and spy_ret is not None) else None
            )

        # Maturity: too young to grade (one session of wiggle isn't an outcome).
        ds = rec["days_since"]
        rec["outcome_maturing"] = (ds is not None and ds < min_days)

        out.append(rec)
    return out


# ── Rollup metrics ──────────────────────────────────────────────────────────

def summary_stats(enriched: list[dict]) -> dict:
    """
    Headline metrics across the supplied recs (already filtered by caller for
    the desired date range / rec_type / etc.):

      n_total          : int
      n_acted          : int
      action_rate      : float (0–100) — n_acted / n_total
      n_wins / losses  : among priced outcomes
      best_pct / worst : largest gain / largest loss outcome
      avg_acted_pct    : mean outcome_pct for MATURE acted with priced outcome
      avg_missed_pct   : mean outcome_pct for MATURE missed with priced outcome
      missed_alpha     : avg_missed_pct - avg_acted_pct (positive = leaving money on table)
      avg_acted_alpha  : mean alpha_pct (vs SPY) for mature acted — the regime-
                         adjusted read of whether acting beat the market
      avg_missed_alpha : mean alpha_pct (vs SPY) for mature missed
      n_maturing       : count of recs too young to grade (excluded from the
                         outcome means; still counted in n_total / n_acted)

    Action rate (n_acted / n_total) counts ALL recs — acting is known the day a
    rec surfaces. Only the OUTCOME aggregates exclude maturing recs (an outcome
    needs time to mean anything).
    """
    n_total    = len(enriched)
    n_acted    = sum(1 for r in enriched if r.get("acted_on"))
    n_maturing = sum(1 for r in enriched if r.get("outcome_maturing"))
    # Outcome aggregates: priced AND mature only.
    priced   = [r for r in enriched
                if r.get("outcome_pct") is not None and not r.get("outcome_maturing")]
    wins     = [r for r in priced if r["outcome_label"] == "win"]
    losses   = [r for r in priced if r["outcome_label"] == "loss"]
    acted_priced  = [r for r in priced if r["acted_on"]]
    missed_priced = [r for r in priced if not r["acted_on"]]

    def _mean(items, key="outcome_pct"):
        vals = [it[key] for it in items if it.get(key) is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    avg_acted   = _mean(acted_priced)
    avg_missed  = _mean(missed_priced)
    missed_alpha = (
        round(avg_missed - avg_acted, 2)
        if (avg_missed is not None and avg_acted is not None) else None
    )
    avg_acted_alpha  = _mean(acted_priced,  "alpha_pct")
    avg_missed_alpha = _mean(missed_priced, "alpha_pct")
    # Combined alpha across ALL graded (mature + priced) recs — acted or not.
    # The "did this band/verdict beat SPY?" read, regardless of whether the user
    # acted. Used by the AI Insights entry-quality-by-band bar (by_composite_band).
    avg_alpha = _mean(priced, "alpha_pct")

    best  = max(priced, key=lambda r: r["outcome_pct"]) if priced else None
    worst = min(priced, key=lambda r: r["outcome_pct"]) if priced else None

    def _bw(r):
        return {
            "ticker":      r["ticker"],
            "rec_date":    r["rec_date"],
            "outcome_pct": round(r["outcome_pct"], 2),
            "alpha_pct":   r.get("alpha_pct"),
            "acted_on":    r["acted_on"],
        }

    return {
        "n_total":      n_total,
        "n_acted":      n_acted,
        "n_maturing":   n_maturing,
        "action_rate":  round(n_acted / n_total * 100.0, 1) if n_total else None,
        "n_priced":     len(priced),
        "n_wins":       len(wins),
        "n_losses":     len(losses),
        "avg_acted_pct":    avg_acted,
        "avg_missed_pct":   avg_missed,
        "missed_alpha":     missed_alpha,
        "avg_acted_alpha":  avg_acted_alpha,
        "avg_missed_alpha": avg_missed_alpha,
        "avg_alpha":        avg_alpha,
        "best":  _bw(best)  if best  else None,
        "worst": _bw(worst) if worst else None,
    }


def by_rec_type(enriched: list[dict]) -> dict:
    """
    Action-rate + avg-outcome rollup keyed by rec_type
    ('new_pick' | 'add_winner' | 'buy_candidate').
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        groups[r["rec_type"]].append(r)
    out = {}
    for rt, items in groups.items():
        out[rt] = summary_stats(items)
    return out


def _verdict_bucket(v: str) -> str:
    """Normalize the stored verdict string into a display bucket."""
    s = (v or "").strip().lower()
    if "confirm" in s:
        return "Confirmed"
    if "conflict" in s:
        return "Conflicted"
    if "caution" in s:
        return "Caution"
    if "mixed" in s:
        return "Mixed"
    if "unverified" in s or "verify" in s:
        return "Unverified"
    return "Other / blank"


# Stable display order for the by-verdict rollup (best → worst signal quality).
_VERDICT_ORDER = ["Confirmed", "Conflicted", "Caution", "Mixed", "Unverified", "Other / blank"]


def by_verdict(enriched: list[dict]) -> list[dict]:
    """
    Action-rate + outcome + alpha rollup keyed by verdict bucket. This is the
    engine-quality view: it judges the App's actual recommendations (Confirmed)
    apart from the awareness feed it deliberately surfaces but steers you away
    from (Conflicted / Caution). Returns a list ordered best→worst signal
    quality so the table reads top-down.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        groups[_verdict_bucket(r.get("verdict"))].append(r)
    out: list[dict] = []
    for bucket in _VERDICT_ORDER:
        items = groups.get(bucket)
        if not items:
            continue
        out.append({"verdict": bucket, **summary_stats(items)})
    return out


def by_composite_band(enriched: list[dict]) -> list[dict]:
    """
    Bucket recs by composite_score band (Strong Buy / Buy / Hold-ish / None)
    so the user can see whether higher-composite recs actually convert and
    perform better than lower-composite ones.

    Bands: ≥75 (Strong Buy), 65–74 (Buy), 44–64 (Hold-zone), <44 (Sell-zone),
    None (not scored).
    """
    bands = [
        ("Strong Buy (≥75)",   lambda c: c is not None and c >= 75),
        ("Buy (65–74)",        lambda c: c is not None and 65 <= c < 75),
        ("Hold zone (44–64)",  lambda c: c is not None and 44 <= c < 65),
        ("Sell zone (<44)",    lambda c: c is not None and c < 44),
        ("Unscored",           lambda c: c is None),
    ]
    out: list[dict] = []
    for label, pred in bands:
        bucket = [r for r in enriched if pred(r.get("composite_score"))]
        if not bucket:
            continue
        stats = summary_stats(bucket)
        out.append({"band": label, **stats})
    return out


def distinct_missed(enriched: list[dict], rec_types: tuple = ("new_pick",)) -> list[dict]:
    """
    Collapse MISSED **actionable** recommendations to one row per distinct ticker —
    the honest "new positions you were told to initiate but skipped" view.

    `rec_types` scopes what counts as actionable. Default = ("new_pick",): only names
    surfaced as "New Positions to Initiate" (they cleared all gates). The awareness-
    only "More Buy Candidates" feed (rec_type 'buy_candidate', incl. Conflicted /
    Unverified names the App steers you *away* from) is excluded — skipping those is
    correct behaviour, not a missed opportunity. Pass rec_types=None for all types.

    A ticker counts as missed only if NONE of its surfacings (ANY rec_type) were acted
    on — so a name you bought on a day it surfaced as a buy_candidate is not wrongly
    flagged. But the representative outcome and n_surfaced come from the ACTIONABLE
    surfacings only, taken from the EARLIEST priced + mature one (the first time the
    App told you to initiate it = the full opportunity window). Younger-than-maturity
    surfacings are ignored for the outcome but still counted toward n_surfaced.

    `outcome_dollars` here is per-$1k NOTIONAL (compute_outcomes normalises missed
    recs to $1000) — the honest cross-ticker magnitude, NOT a portfolio-level claim
    (you cannot buy every surfaced name; capital + concentration caps + gates bind).

    Returns rows sorted by alpha_pct desc (biggest missed winners first), each:
        ticker, first_rec_date, n_surfaced, verdict, outcome_pct, alpha_pct,
        outcome_dollars (per-$1k), outcome_label
    Tickers with no actionable priced + mature surfacing are skipped (can't grade).
    """
    by_tk: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        by_tk[r["ticker"]].append(r)

    rows: list[dict] = []
    for tk, items in by_tk.items():
        if any(r.get("acted_on") for r in items):
            continue   # acted on via ANY surfacing → not a missed name
        # Actionable surfacings only (default: New Positions to Initiate).
        pool = [r for r in items if (rec_types is None or r.get("rec_type") in rec_types)]
        gradable = [
            r for r in pool
            if r.get("outcome_pct") is not None
            and not r.get("outcome_maturing")
            and r.get("rec_date") is not None
        ]
        if not gradable:
            continue
        rep = min(gradable, key=lambda r: r["rec_date"])
        rows.append({
            "ticker":          tk,
            "first_rec_date":  rep["rec_date"],
            "n_surfaced":      len(pool),
            "verdict":         rep.get("verdict") or "",
            "outcome_pct":     round(rep["outcome_pct"], 2),
            "alpha_pct":       rep.get("alpha_pct"),
            "outcome_dollars": rep.get("outcome_dollars"),
            "outcome_label":   rep.get("outcome_label"),
        })

    rows.sort(
        key=lambda x: (x["alpha_pct"] if x["alpha_pct"] is not None else x["outcome_pct"]),
        reverse=True,
    )
    return rows


def missed_split(distinct_rows: list[dict]) -> dict:
    """
    Summarise distinct-missed names into 'missed winners' (rose — a process miss)
    vs 'dodged losers' (fell — discretion paid off), with magnitudes. Split on
    outcome_label (win/loss by ±0.5%); alpha is the regime-adjusted companion read.

    All dollar figures are per-$1k notional (see distinct_missed) — selection
    quality, never a portfolio-level "you would have gained X%" counterfactual.
    """
    winners = [r for r in distinct_rows if r.get("outcome_label") == "win"]
    dodged  = [r for r in distinct_rows if r.get("outcome_label") == "loss"]
    flats   = [r for r in distinct_rows if r.get("outcome_label") == "flat"]

    def _avg(items, key):
        vals = [it[key] for it in items if it.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    biggest_miss  = max(distinct_rows, key=lambda r: r["outcome_pct"]) if distinct_rows else None
    biggest_dodge = min(distinct_rows, key=lambda r: r["outcome_pct"]) if distinct_rows else None

    return {
        "n_distinct":       len(distinct_rows),
        "n_winners":        len(winners),
        "n_dodged":         len(dodged),
        "n_flat":           len(flats),
        "avg_winner_pct":   _avg(winners, "outcome_pct"),
        "avg_winner_alpha": _avg(winners, "alpha_pct"),
        "avg_dodged_pct":   _avg(dodged, "outcome_pct"),
        "avg_dodged_alpha": _avg(dodged, "alpha_pct"),
        "avg_per_1k":       _avg(distinct_rows, "outcome_dollars"),
        "biggest_miss":     biggest_miss,
        "biggest_dodge":    biggest_dodge,
    }


def signal_flow(enriched: list[dict], rec_types: tuple = ("new_pick",)) -> dict:
    """
    Distinct-ticker funnel for the signal-flow Sankey:
        actionable recs → acted / missed → win / loss / flat-or-open

    Consistent with `distinct_missed` and the rest of the page:
    - **Distinct by TICKER**, not instance counts — a name surfaced 15× counts once
      (avoids the surfacings-vs-distinct inflation).
    - Scoped to `rec_types` (default ("new_pick",) = New Positions to Initiate); the
      awareness-only buy_candidate feed is excluded.
    - **Acted** if the ticker was acted on via ANY surfacing (same safeguard as
      distinct_missed — a name bought on a buy-candidate day isn't called missed).
    - Win/loss counted on **MATURE** outcomes only (`outcome_maturing` excluded, per
      REC_SCORE_MIN_DAYS); maturing / ungraded tickers fall into 'flat-or-open' so the
      Sankey can never disagree with the win/loss aggregates elsewhere on the page.
      Representative = earliest mature surfacing (acted: earliest mature acted one).

    Returns a flat dict of counts:
        n_total, n_acted, n_missed,
        acted_win, acted_loss, acted_flat,
        missed_win, missed_loss, missed_flat
    """
    acted_tickers = {r["ticker"] for r in enriched if r.get("acted_on")}
    by_tk: dict[str, list[dict]] = defaultdict(list)
    for r in enriched:
        if rec_types is None or r.get("rec_type") in rec_types:
            by_tk[r["ticker"]].append(r)

    out = {
        "n_total": 0, "n_acted": 0, "n_missed": 0,
        "acted_win": 0, "acted_loss": 0, "acted_flat": 0,
        "missed_win": 0, "missed_loss": 0, "missed_flat": 0,
    }

    def _bucket(items, acted: bool):
        mature = [
            r for r in items
            if r.get("outcome_pct") is not None
            and not r.get("outcome_maturing")
            and r.get("rec_date") is not None
        ]
        pool = [r for r in mature if r.get("acted_on")] if acted else mature
        pool = pool or mature
        rep = min(pool, key=lambda r: r["rec_date"]) if pool else None
        return rep["outcome_label"] if rep else None

    for tk, items in by_tk.items():
        out["n_total"] += 1
        if tk in acted_tickers:
            out["n_acted"] += 1
            lbl = _bucket(items, acted=True)
            out["acted_win"]  += lbl == "win"
            out["acted_loss"] += lbl == "loss"
            out["acted_flat"] += lbl not in ("win", "loss")
        else:
            out["n_missed"] += 1
            lbl = _bucket(items, acted=False)
            out["missed_win"]  += lbl == "win"
            out["missed_loss"] += lbl == "loss"
            out["missed_flat"] += lbl not in ("win", "loss")

    return out


def report_viz_snapshot(enriched: list[dict], rec_types: tuple = ("new_pick",)) -> dict:
    """
    Everything the F-4 Monthly report's THREE visuals need, computed from ONE
    full (all-rec_type) enriched set so the decision-flow Sankey, the alpha-by-band
    bar, and the ranked missed-opportunity bar can never disagree with each other,
    with the headline counts, or with the Recommendations History page.

    Pass the FULL enriched (every rec_type in the window): `signal_flow` and
    `distinct_missed` detect "acted on via ANY surfacing" across rec_types (so a name
    bought on a buy-candidate day isn't mislabelled missed) and then scope the COUNTED
    names to `rec_types`. `by_composite_band` has no rec_type arg, so it is scoped here.

    The return is JSON-serialisable (ints, floats, strings, None) so it can be FROZEN
    alongside the saved report (db column `viz_json`) and re-rendered verbatim later —
    making a monthly report an immutable dated artifact rather than a live recompute
    that drifts as prices move and names mature. The app re-renders this same shape on
    the live-fallback path for reports saved before freezing.

        {
          "flow":         {n_total, n_acted, n_missed,
                           acted_win/loss/flat, missed_win/loss/flat},   # signal_flow
          "bands":        [{"band": str, "avg_alpha": float}, ...],      # scoped, avg_alpha not None
          "missed":       [{"ticker": str, "outcome_pct": float}, ...],  # distinct missed, for the bar
          "missed_split": {n_distinct, n_winners, n_dodged, n_flat},     # JSON-safe scalars only
        }
    """
    flow   = signal_flow(enriched, rec_types=rec_types)
    scoped = [r for r in enriched if rec_types is None or r.get("rec_type") in rec_types]
    bands  = [
        {"band": b["band"], "avg_alpha": b["avg_alpha"]}
        for b in by_composite_band(scoped)
        if b.get("avg_alpha") is not None
    ]
    missed_rows = distinct_missed(enriched, rec_types=rec_types)
    missed = [
        {"ticker": r["ticker"], "outcome_pct": r["outcome_pct"]}
        for r in missed_rows
    ]
    split = missed_split(missed_rows)
    # Keep only JSON-safe scalars from the split (biggest_miss/dodge carry date objects).
    missed_split_safe = {
        "n_distinct": split["n_distinct"],
        "n_winners":  split["n_winners"],
        "n_dodged":   split["n_dodged"],
        "n_flat":     split["n_flat"],
    }
    return {
        "flow":         flow,
        "bands":        bands,
        "missed":       missed,
        "missed_split": missed_split_safe,
    }


def engine_trust_by_band(enriched: list[dict]) -> list[dict]:
    """
    Action rate and alpha quality broken down by composite score band.

    Answers: "Did you trust the engine more at 75+ vs 65-74, and were you
    right to?" — the key self-awareness question for systematic investing.

    Bands:
      < 65   — below buy threshold (should not have been acted on; interesting if so)
      65–74  — BUY tier
      75+    — STRONG BUY tier

    Returns list of band dicts sorted by band floor ascending.  Each dict:
      band_label      — str display label
      band_floor      — int (for sorting)
      n_recs          — int  (mature + priced; excludes maturing rows)
      n_acted         — int
      action_rate     — float %
      avg_alpha_acted — float | None  (avg alpha_pct for acted rows with alpha)
      avg_alpha_passed— float | None  (avg alpha_pct for passed rows with alpha)
      edge_comment    — str plain-English read of the alpha comparison
    """
    def _band(score):
        try:
            s = float(score)
        except (TypeError, ValueError):
            return None
        if s < 65:
            return (0, "Below 65 (sub-threshold)")
        if s < 75:
            return (65, "65–74 (BUY)")
        return (75, "75+ (Strong BUY)")

    buckets: dict[int, dict] = {}
    for r in enriched:
        if r.get("outcome_maturing"):
            continue
        result = _band(r.get("composite_score"))
        if result is None:
            continue
        floor, label = result
        if floor not in buckets:
            buckets[floor] = {
                "band_label":   label,
                "band_floor":   floor,
                "_n":           0,
                "_acted":       0,
                "_alpha_acted": [],
                "_alpha_passed":[],
            }
        b = buckets[floor]
        b["_n"] += 1
        alpha = r.get("alpha_pct")
        if r["acted_on"]:
            b["_acted"] += 1
            if alpha is not None:
                b["_alpha_acted"].append(float(alpha))
        else:
            if alpha is not None:
                b["_alpha_passed"].append(float(alpha))

    rows = []
    for floor, b in sorted(buckets.items()):
        n      = b["_n"]
        acted  = b["_acted"]
        ar     = round(acted / n * 100, 1) if n else 0.0
        aa     = round(sum(b["_alpha_acted"])  / len(b["_alpha_acted"]),  1) if b["_alpha_acted"]  else None
        ap     = round(sum(b["_alpha_passed"]) / len(b["_alpha_passed"]), 1) if b["_alpha_passed"] else None

        if aa is not None and ap is not None:
            if aa > ap:
                edge = f"Acting on this band delivered {aa - ap:+.1f}pp more alpha than passing — engine was right."
            elif ap > aa:
                edge = f"Passing on this band outperformed acting by {ap - aa:.1f}pp — you may be over-trusting the engine here."
            else:
                edge = "Acting and passing produced similar alpha — no clear edge signal."
        elif aa is not None:
            edge = f"Acted rows: avg {aa:+.1f}pp alpha. (No passed rows with outcomes to compare.)"
        elif ap is not None:
            edge = f"Passed rows: avg {ap:+.1f}pp alpha. (No acted rows with outcomes to compare.)"
        else:
            edge = "Insufficient outcome data to draw conclusions."

        rows.append({
            "band_label":       b["band_label"],
            "band_floor":       floor,
            "n_recs":           n,
            "n_acted":          acted,
            "action_rate":      ar,
            "avg_alpha_acted":  aa,
            "avg_alpha_passed": ap,
            "edge_comment":     edge,
        })

    return rows


def daily_volume(enriched: list[dict]) -> list[dict]:
    """
    For the recs-per-day chart: count of recs surfaced per rec_date, split
    by acted vs not. Returns list of {date, total, acted, missed} sorted by date.
    """
    by_day: dict[date, dict] = defaultdict(lambda: {"total": 0, "acted": 0, "missed": 0})
    for r in enriched:
        d = r.get("rec_date")
        if d is None:
            continue
        by_day[d]["total"] += 1
        if r["acted_on"]:
            by_day[d]["acted"] += 1
        else:
            by_day[d]["missed"] += 1
    return [
        {"date": d, **counts}
        for d, counts in sorted(by_day.items(), key=lambda x: x[0])
    ]
