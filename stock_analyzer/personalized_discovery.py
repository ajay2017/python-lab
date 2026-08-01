"""
Personalized Discovery — flags which of today's already-gated Grow Today
picks resemble the user's own REALIZED winning trades.

Runs Behavioral Fingerprint's (F-193) backward-looking analysis FORWARD:
Behavioral Fingerprint only ever compares two buckets of past decisions
against each other (e.g. high-momentum vs low-momentum action rate) — it has
no notion of "what did a typical winner look like at entry." This module
builds that profile from realized (closed, is_gain=True) trade lots joined
back to the matched recommendation at entry, then checks new candidates
against it.

Zero new fetches: entirely a replay of already-loaded trades_df/recs_df via
investor_mirror.build_closed_lots() and
recommendations_history.match_recs_to_trades(). Deliberately avoids
recommendations_history.compute_outcomes()'s mark-to-market path, which
would need a live current price for every historically-recommended ticker
(including ones no longer held) — a real, avoidable fetch-cost problem this
design sidesteps by working off realized lots only.

Pure computation module. No Streamlit, no DB, no yfinance calls. Diagnostic/
awareness only — never gates, re-scores, or re-ranks a recommendation.
"""

import pandas as pd


def build_winner_profile(
    closed_lots: pd.DataFrame,
    matched_recs: list[dict],
    min_n: int,
    pctl_low: float = 25,
    pctl_high: float = 75,
) -> dict | None:
    """
    Aggregate the composite score / momentum score / sector of every REALIZED
    winning trade lot (from investor_mirror.build_closed_lots()) that joins
    back to a matched, acted-on new_pick/add_winner recommendation at entry
    (from recommendations_history.match_recs_to_trades()) — i.e. what a
    typical winning entry looked like for THIS user.

    Join key: ticker + rec_date == buy_date, since match_recs_to_trades()
    only matches a rec to a SAME-DAY trade (rec_date == traded_at date), so a
    winning lot's buy_date lines up exactly with the rec_date of whichever
    rec (if any) triggered that entry.

    A "winning lot" requires BOTH is_gain=True AND a non-null pnl_pct (a
    null pnl_pct means missing price data, which build_closed_lots's
    `is_gain = (pnl_abs or 0.0) >= 0` would otherwise misreport as a gain).
    Multiple sell-fragments of the SAME entry (a position scaled out across
    several profitable sells) collapse to one sample per (ticker, buy_date)
    before counting — one entry decision must count once, not once per
    fragment.

    Returns None below `min_n` matched winning ENTRIES — withheld, never a
    profile fabricated off a handful of trades.

    Returns a dict: composite_low/composite_high, momentum_low/momentum_high
    (the pctl_low/pctl_high percentile band of matched scores — None for
    either band if no matched entry had that score recorded), top_sectors (a
    set of sectors appearing in >=2 winning entries, or the single most
    common sector if none repeat), n (sample size, matched winning entries).
    """
    if closed_lots is None or closed_lots.empty:
        return None

    # Index matched, acted-on, actionable recs by (ticker, rec_date) for the join.
    rec_lookup: dict[tuple[str, object], dict] = {}
    for r in (matched_recs or []):
        if not r.get("acted_on"):
            continue
        if r.get("rec_type") not in ("new_pick", "add_winner"):
            continue
        key = (str(r.get("ticker", "")).upper(), r.get("rec_date"))
        rec_lookup.setdefault(key, r)

    # is_gain ALONE is insufficient: build_closed_lots's `is_gain =
    # (pnl_abs or 0.0) >= 0` evaluates True when pnl_abs is None (missing
    # price data) — drop rows with no real pnl_pct first, same guard
    # investor_mirror.py's own winner-filtering functions already apply
    # (e.g. premature_exit_cost).
    valid = closed_lots.dropna(subset=["pnl_pct"])
    winners = valid[valid["is_gain"].astype(bool)]
    if winners.empty:
        return None

    # Collapse multiple sell-fragments of the SAME entry (a position scaled
    # out across several profitable sells) to one row per (ticker, buy_date)
    # BEFORE counting/joining — build_closed_lots emits one row per matched
    # sell fragment, so without this a single entry scaled out 3x would
    # triple-count as 3 "winning entries," inflating n past
    # BEHAVIORAL_MIN_SAMPLE_N and triple-weighting its scores/sector in the
    # bands below. All fragments of one entry share the same (ticker,
    # buy_date) and therefore the same matched rec, so any one representative
    # row per group is equivalent for this join.
    winning_entries = winners.drop_duplicates(subset=["ticker", "buy_date"])

    composites: list[float] = []
    momentums: list[float] = []
    sectors: list[str] = []
    n = 0   # count of matched winning ENTRIES, NOT of any one trait — an
            # entry that joined to a rec but is missing e.g. sector is still
            # a genuine sample point, just one that can't inform every band.

    for _, lot in winning_entries.iterrows():
        key = (str(lot["ticker"]).upper(), lot["buy_date"])
        rec = rec_lookup.get(key)
        if rec is None:
            continue
        n += 1
        if rec.get("composite_score") is not None:
            composites.append(float(rec["composite_score"]))
        if rec.get("momentum_score") is not None:
            momentums.append(float(rec["momentum_score"]))
        if rec.get("sector"):
            sectors.append(str(rec["sector"]))

    if n < min_n:
        return None

    composite_low = composite_high = None
    if composites:
        s = pd.Series(composites)
        composite_low = float(s.quantile(pctl_low / 100.0))
        composite_high = float(s.quantile(pctl_high / 100.0))

    momentum_low = momentum_high = None
    if momentums:
        s = pd.Series(momentums)
        momentum_low = float(s.quantile(pctl_low / 100.0))
        momentum_high = float(s.quantile(pctl_high / 100.0))

    top_sectors: set[str] = set()
    if sectors:
        counts = pd.Series(sectors).value_counts()
        repeated = counts[counts >= 2]
        top_sectors = (
            {str(s) for s in repeated.index}
            if not repeated.empty else {str(counts.index[0])}
        )

    return {
        "composite_low":  composite_low,
        "composite_high": composite_high,
        "momentum_low":   momentum_low,
        "momentum_high":  momentum_high,
        "top_sectors":    top_sectors,
        "n":              n,
    }


def score_candidate_match(
    composite_score: float | None,
    momentum_score: float | None,
    sector: str | None,
    profile: dict | None,
) -> dict:
    """
    Check a candidate ticker's entry stats against a winner profile. Each
    trait is checked independently and never blended into a single
    fabricated score.

    Returns {"matched_traits": [...], "n_matched": int}. Empty/zero when
    `profile` is None or the candidate has no comparable data.
    """
    if not profile:
        return {"matched_traits": [], "n_matched": 0}

    matched: list[str] = []

    clo, chi = profile.get("composite_low"), profile.get("composite_high")
    if composite_score is not None and clo is not None and chi is not None:
        if clo <= float(composite_score) <= chi:
            matched.append("composite tier")

    mlo, mhi = profile.get("momentum_low"), profile.get("momentum_high")
    if momentum_score is not None and mlo is not None and mhi is not None:
        if mlo <= float(momentum_score) <= mhi:
            matched.append("momentum")

    top_sectors = profile.get("top_sectors") or set()
    if sector and top_sectors and sector in top_sectors:
        matched.append("sector")

    return {"matched_traits": matched, "n_matched": len(matched)}
