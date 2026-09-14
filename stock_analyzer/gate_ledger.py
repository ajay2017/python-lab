"""Gate Suppression Ledger — capture-half builder.

Pure. No DB, no Streamlit, no clock reads. Takes `today` as an argument.
Called by cron_runner._run_scan (source="cron") and app.py (source="app")
after each Grow Today build.

Naming / lane semantics:
  - "new_pick"   : sites 1-2 (macro_blocked_picks, sector_blocked_picks)
  - "add_winner" : sites 3-9 (all add-to-winner suppression buckets)
  - "tone"       : G-23 bear-day synthetic row
  - "downgrade"      : G-05/G-06/G-13 (roadmap B2, 2026-09-13) — the name
    still renders, just weaker (Watchlist ENTER_NOW downgraded to
    NEAR_ENTRY). Alpha here does NOT mean "cost of not showing it" — the
    card was shown, just with a caveat.
  - "add_suppressed" : G-02/G-18 (roadmap B2) — a true suppression, same
    claim shape as "add_winner": the add vanished entirely (Rebalancer ADD,
    or Analysis's add-to-position sizing block).

sentinel contract — MUST branch `is None` vs `[]`:
  - grow is None  → offline; return [] (gates never ran — do not record)
  - grow is {}    → gates ran; no bucket key → no rows for that bucket
  - bucket is []  → gates ran; bucket checked and found nothing
  Both {} and [] yield no rows, but they are semantically distinct and
  must remain reachable separately in tests.

Roadmap B2 (2026-09-13): three additional pure builders below —
`build_watchlist_suppression_rows`, `build_rebalance_suppression_rows`,
`build_analysis_stop_suppression_row` — cover G-02/G-05/G-06/G-13/G-18, none
of which flow through `grow_today` (they live in watchlist_advisor.py,
rebalancer.py, and inline in app.py's Analysis page). `build_suppression_rows`
above is untouched by this addition. All 5 gate id literals are centralized
here (see `_WATCHLIST_KIND`) so tests/test_gate_registry.py's literal scan of
this file continues to cover them.
"""
from __future__ import annotations

from stock_analyzer.constants import MARKET_TONE_BEAR_PCT

# Bucket name → lane label.
_BUCKET_LANES: dict[str, str] = {
    "macro_blocked_picks":       "new_pick",
    "sector_blocked_picks":      "new_pick",
    "sector_blocked_adds":       "add_winner",
    "risk_blocked_adds":         "add_winner",
    "concentration_blocked_adds": "add_winner",
    "cooldown_adds":             "add_winner",
    "deterioration_blocked_adds": "add_winner",
}

_MAX_REASON_LEN = 300


def build_suppression_rows(
    grow: "dict | None",
    *,
    rec_date,
    source: str,
    tone: "str | None" = None,
    sp500_pct: "float | None" = None,
) -> "list[dict]":
    """Build the rows to upsert into gate_suppressions.

    Parameters
    ----------
    grow:
        The grow_today dict returned by build_daily_briefing, or None when
        offline.  `None` means the gates never ran — return [] without
        recording anything.
    rec_date:
        The briefing date (date object or ISO string).
    source:
        "cron" or "app" — goes into the dedup key.
    tone:
        Market tone from the brief ("bull" | "flat" | "bear" | None).
    sp500_pct:
        S&P 500 same-day % change — used for the G-23 bear-day row.
    """
    # Offline: gates never ran. Recording "nothing suppressed" would be a lie.
    if grow is None:
        return []

    rec_date_str = (
        rec_date.isoformat() if hasattr(rec_date, "isoformat") else str(rec_date)[:10]
    )

    # Bear day: the add + pick lane early-returns before any bucket is built,
    # so all buckets are absent (not just empty). Emit exactly ONE synthetic row
    # for the tone gate — the binding suppression that day.
    if tone == "bear":
        return [
            {
                "ticker":          "__MARKET__",
                "gate_id":         "G-23",
                "lane":            "tone",
                "counterfactual":  True,
                "gate_value":      sp500_pct,
                "gate_threshold":  MARKET_TONE_BEAR_PCT,
                "tone":            "bear",
                "composite_score": None,
                "momentum_score":  None,
                "sector":          None,
                "price_at_suppress": None,
                "reason":          None,
                "rec_date":        rec_date_str,
                "source":          source,
            }
        ]

    rows: list[dict] = []

    for bucket_name, lane in _BUCKET_LANES.items():
        bucket = grow.get(bucket_name)
        # Both None (key absent) and [] yield no rows; both are valid — the
        # caller's tests must reach each path to prove this is branched, not
        # collapsed.
        if not bucket:
            continue
        for item in bucket:
            gate_id = item.get("gate_id")
            if gate_id is None:
                # F4: never infer gate_id from the bucket name. Skip unlabelled
                # items — a missing label means step 2 missed a site, and a wrong
                # id is worse than a missing row.
                continue

            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker:
                continue

            # price_at_suppress: NULL when <= 0, per price_at_surface convention.
            # The producer stores the price as "price" (added at all 9 sites).
            _price = item.get("price")
            try:
                price_val: "float | None" = float(_price) if _price is not None else None
                if price_val is not None and price_val <= 0:
                    price_val = None
            except (TypeError, ValueError):
                price_val = None

            # reason: free text, truncated to 300 chars. Nothing may ever parse it.
            _reason = item.get("reason")
            reason = str(_reason)[:_MAX_REASON_LEN] if _reason is not None else None

            # Scores are explicit at the producer (daily_briefing.py sites 1-9).
            # Read directly; never infer from lane or the generic "score" key.
            composite_score = item.get("composite_score")
            momentum_score  = item.get("momentum_score")

            rows.append({
                "ticker":           ticker,
                "gate_id":          gate_id,
                "lane":             lane,
                "counterfactual":   item.get("counterfactual"),
                "gate_value":       item.get("gate_value"),
                "gate_threshold":   item.get("gate_threshold"),
                "tone":             tone,
                "composite_score":  composite_score,
                "momentum_score":   momentum_score,
                "sector":           item.get("sector"),
                "price_at_suppress": price_val,
                "reason":           reason,
                "rec_date":         rec_date_str,
                "source":           source,
            })

    return rows


# ── Roadmap B2 (2026-09-13): G-02/G-05/G-06/G-13/G-18 builders ─────────────
#
# All 5 gate id literals are centralized here — never scattered across
# watchlist_advisor.py / rebalancer.py / app.py — so
# tests/test_gate_registry.py's literal scan of this file continues to catch
# a typo'd id with zero test-file changes.

# Watchlist card `suppression_kind` -> gate id. G-05/G-06 share one
# hard-breach card shape at watchlist_advisor.py (discriminated only by
# `gate["kind"]`); G-13 is the separate in-zone-R:R downgrade card. An
# ordinary NEAR_ENTRY card (e.g. the "approaching zone" branch) carries no
# `suppression_kind` at all and must never reach a row — see the builder's
# own guard below.
_WATCHLIST_KIND: dict[str, str] = {
    "sector": "G-05",
    "beta":   "G-06",
    "rr":     "G-13",
}

# Short, fixed reason text per suppression_kind — the watchlist card itself
# already carries a long user-facing narrative (title/summary/detail); this
# is the ledger's own compact record, same shape as G-01's hardcoded reason.
_WATCHLIST_REASON: dict[str, str] = {
    "sector": "Sector at hard ceiling — ENTER_NOW downgraded to NEAR_ENTRY.",
    "beta":   "Portfolio beta + ticker beta both breached — ENTER_NOW downgraded to NEAR_ENTRY.",
    "rr":     "ENTER_NOW without a validated R:R — downgraded to NEAR_ENTRY.",
}


def build_watchlist_suppression_rows(
    recs: "list[dict] | None",
    *,
    rec_date,
    source: str,
    sector_by_ticker: "dict | None" = None,
) -> "list[dict]":
    """Build G-05/G-06/G-13 rows from Watchlist recommendation cards.

    `recs`: the list build_watchlist_recommendation() produces, one dict per
    ticker. Only a card that was actually downgraded carries a
    `suppression_kind` key (set at watchlist_advisor.py's hard-breach and
    in-zone-R:R sites) — an ordinary ENTER_NOW/NEAR_ENTRY (approaching-zone)/
    WAIT_*/REMOVE/DATA_UNAVAILABLE card has no such key and is skipped. Both
    conditions (`action == "NEAR_ENTRY"` AND `suppression_kind` present) are
    checked — belt-and-braces against a future card gaining one without the
    other.

    Sentinel: `recs` is None or [] → return [] (a missing/empty session means
    the gates never ran this pass — recording "nothing suppressed" would be
    a lie, same reasoning as build_suppression_rows' `grow is None` branch).
    """
    if not recs:
        return []

    rec_date_str = (
        rec_date.isoformat() if hasattr(rec_date, "isoformat") else str(rec_date)[:10]
    )
    sector_by_ticker = sector_by_ticker or {}

    rows: list[dict] = []
    for card in recs:
        if card.get("action") != "NEAR_ENTRY":
            continue
        kind = card.get("suppression_kind")
        if not kind:
            continue
        gate_id = _WATCHLIST_KIND.get(kind)
        if gate_id is None:
            # Unknown suppression_kind — never guess a gate id (F4 precedent
            # from build_suppression_rows above: a wrong id is worse than a
            # missing row).
            continue

        ticker = str(card.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        # price_at_suppress: NULL when <= 0, per price_at_surface convention.
        _price = card.get("price")
        try:
            price_val: "float | None" = float(_price) if _price is not None else None
            if price_val is not None and price_val <= 0:
                price_val = None
        except (TypeError, ValueError):
            price_val = None

        rows.append({
            "ticker":            ticker,
            "gate_id":           gate_id,
            "lane":              "downgrade",
            "counterfactual":    True,
            "gate_value":        card.get("gate_value"),
            "gate_threshold":    card.get("gate_threshold"),
            "tone":              None,
            "composite_score":   card.get("score"),
            "momentum_score":    None,
            "sector":            sector_by_ticker.get(ticker),
            "price_at_suppress": price_val,
            "reason":            _WATCHLIST_REASON.get(kind),
            "rec_date":          rec_date_str,
            "source":            source,
        })

    return rows


def build_rebalance_suppression_rows(
    risk_blocked_adds: "list[dict] | None",
    *,
    rec_date,
    source: str,
) -> "list[dict]":
    """Build G-02 rows from the Rebalancer's `risk_blocked_adds` bucket.

    `risk_blocked_adds`: rebalancer.build_rebalance_plan()'s
    "risk_blocked_adds" list — ADDs suppressed because Risk Advisor is
    already recommending a TRIM on the same ticker. A set-membership gate,
    like G-01 — no scalar gate_value/gate_threshold (gate_value=None,
    gate_threshold=None, matching G-01's own shape).

    Sentinel: `risk_blocked_adds` is None or [] → return [] (nothing to
    record — either the plan never ran, or genuinely nothing was blocked;
    the caller cannot distinguish the two from this bucket alone, same as
    every other bucket in build_suppression_rows).
    """
    if not risk_blocked_adds:
        return []

    rec_date_str = (
        rec_date.isoformat() if hasattr(rec_date, "isoformat") else str(rec_date)[:10]
    )

    rows: list[dict] = []
    for item in risk_blocked_adds:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        _price = item.get("price")
        try:
            price_val: "float | None" = float(_price) if _price is not None else None
            if price_val is not None and price_val <= 0:
                price_val = None
        except (TypeError, ValueError):
            price_val = None

        _reason = item.get("reason")
        reason = str(_reason)[:_MAX_REASON_LEN] if _reason is not None else None

        rows.append({
            "ticker":            ticker,
            "gate_id":           "G-02",
            "lane":              "add_suppressed",
            "counterfactual":    True,
            "gate_value":        None,
            "gate_threshold":    None,
            "tone":              None,
            "composite_score":   item.get("composite_score"),
            "momentum_score":    None,
            "sector":            item.get("sector"),
            "price_at_suppress": price_val,
            "reason":            reason,
            "rec_date":          rec_date_str,
            "source":            source,
        })

    return rows


def build_analysis_stop_suppression_row(
    *,
    ticker: str,
    price,
    composite_score,
    stop,
    gap_pct,
    sector,
    rec_date,
    source: str,
) -> "dict | None":
    """Build one G-18 row from the Analysis page's stop-breach block.

    G-18: a held position with a breached stop suppresses the Analysis
    page's "add to position" sizing block (the red "⛔ Stop breached" banner
    already renders unconditionally — this call only CAPTURES that it fired).
    gate_value=price (the price that breached the stop), gate_threshold=stop.

    Sentinel: empty/blank `ticker` → return None (nothing to record — never
    a partial row).
    """
    ticker = str(ticker or "").strip().upper()
    if not ticker:
        return None

    rec_date_str = (
        rec_date.isoformat() if hasattr(rec_date, "isoformat") else str(rec_date)[:10]
    )

    # price_at_suppress: NULL when <= 0, per price_at_surface convention.
    try:
        price_val: "float | None" = float(price) if price is not None else None
        if price_val is not None and price_val <= 0:
            price_val = None
    except (TypeError, ValueError):
        price_val = None

    _gap_str = f"{gap_pct:+.1f}%" if isinstance(gap_pct, (int, float)) else "n/a"
    reason = (
        f"Held position stop breached (gap {_gap_str}) — add-on sizing suppressed."
    )[:_MAX_REASON_LEN]

    return {
        "ticker":            ticker,
        "gate_id":           "G-18",
        "lane":              "add_suppressed",
        "counterfactual":    True,
        "gate_value":        price,
        "gate_threshold":    stop,
        "tone":              None,
        "composite_score":   composite_score,
        "momentum_score":    None,
        "sector":            sector,
        "price_at_suppress": price_val,
        "reason":            reason,
        "rec_date":          rec_date_str,
        "source":            source,
    }
